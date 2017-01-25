# This is essentially a slimmed down mapreduce. There are some differences with the sharding logic
# and the whole thing leverages defer and there's no reducing, just mapping.

# If you're wondering why we're not using MR here...
# 1. We don't want a hard dependency on it and migrations are core (unlike stuff in contrib)
# 2. MR is massive overkill for what we're doing here

import cPickle
import logging

from django.conf import settings
from google.appengine.api import datastore, datastore_errors
from google.appengine.ext import deferred


def _next_key(key):
    val = key.id_or_name()
    if isinstance(val, basestring):
        return datastore.Key.from_path(
            key.kind(),
            val + chr(1),
            namespace=key.namespace()
        )
    else:
        return datastore.Key.from_path(
            key.kind(),
            val + 1,
            namespace=key.namespace()
        )

def _mid_key(key1, key2):
    val = key1.id_or_name()
    if isinstance(val, basestring):
        raise NotImplementedError("need to implement key names")
    else:
        return datastore.Key.from_path(
            key1.kind(),
            val + ((key2.id_or_name() - val) // 2),
            namespace=key1.namespace()
        )

def _generate_shards(keys, shard_count):
    keys = sorted(keys) # Ensure the keys are sorted

    # Special case single key
    if shard_count == 1:
        return [[keys[0], keys[-1]]]
    elif shard_count < len(keys):
        index_stride = len(keys) / float(shard_count)
        keys = [keys[int(round(index_stride * i))] for i in range(1, shard_count)]

    shards = []
    for i in xrange(len(keys) - 1):
        shards.append([keys[i], keys[i + 1]])

    return shards


def _find_largest_shard(shards):
    """
        Given a list of shards, find the one with the largest ID range
    """
    largest_shard = None

    for shard in shards:
        if largest_shard is None:
            largest_shard = shard
        else:
            current_range = largest_shard[1].id_or_name() - largest_shard[0].id_or_name()
            this_range = shard[1].id_or_name() - shard[0].id_or_name()
            if this_range > current_range:
                largest_shard = shard

    return largest_shard


def shard_query(query, shard_count):
    OVERSAMPLING_MULTIPLIER = 32 # This value is used in Mapreduce

    try:
        query.Order("__key__")
        min_id = query.Run().next().key()

        query.Order(("__key__", query.DESCENDING))
        max_id = query.Run().next().key()
    except StopIteration:
        # No objects, so no shards
        return []

    query.Order("__scatter__") # Order by the scatter property

    # Get random keys to shard on
    keys = [ x.key() for x in query.Get(shard_count * OVERSAMPLING_MULTIPLIER) ]
    keys.sort()

    if not keys: # If no keys...
        # Shard on the upper and lower PKs in the query this is *not* efficient
        keys = [min_id, max_id]
    else:
        if keys[0] != min_id:
            keys.insert(0, min_id)

        if keys[-1] != max_id:
            keys.append(max_id)

    shards = _generate_shards(keys, shard_count)
    while True:
        if len(shards) >= shard_count:
            break

        # If we don't have enough shards, divide the largest key range until we have enough
        largest_shard = _find_largest_shard(shards)

        # OK we can't shard anymore, just bail
        if largest_shard[0] == largest_shard[1]:
            break

        left_shard = [
            largest_shard[0],
            _mid_key(largest_shard[0], largest_shard[1])
        ]

        right_shard = [
            _next_key(_mid_key(largest_shard[0], largest_shard[1])),
            largest_shard[1]
        ]

        # We already have these shards, just give up now
        if left_shard in shards and right_shard in shards:
            break

        shards.remove(largest_shard)
        if left_shard not in shards:
            shards.append(left_shard)

        if right_shard not in shards:
            shards.append(right_shard)
        shards.sort()

    assert len(shards) <= shard_count

    # We shift the end keys by one, so we can
    # do a >= && < query
    for shard in shards:
        shard[1] = _next_key(shard[1])

    return shards


class ShardedTaskMarker(datastore.Entity):
    KIND = "_djangae_migration_task"

    QUEUED_KEY = "shards_queued"
    RUNNING_KEY = "shards_running"
    FINISHED_KEY = "shards_finished"

    def __init__(self, identifier, query, *args, **kwargs):
        kwargs["kind"] = self.KIND
        kwargs["name"] = identifier

        super(ShardedTaskMarker, self).__init__(*args, **kwargs)

        self[ShardedTaskMarker.QUEUED_KEY] = []
        self[ShardedTaskMarker.RUNNING_KEY] = []
        self[ShardedTaskMarker.FINISHED_KEY] = []
        self["query"] = cPickle.dumps(query)
        self["is_finished"] = False

    @classmethod
    def get_key(cls, identifier, namespace):
        return datastore.Key.from_path(
            cls.KIND,
            identifier,
            namespace=namespace
        )

    def put(self, *args, **kwargs):
        if not self["is_finished"]:
            # If we aren't finished, see if we are now
            # This if-statement is important because if a task had no shards
            # it would be 'finished' immediately so we don't want to incorrectly
            # set it to False when we save if we manually set it to True
            self["is_finished"] = bool(
                not self[ShardedTaskMarker.QUEUED_KEY] and
                not self[ShardedTaskMarker.RUNNING_KEY] and
                self[ShardedTaskMarker.FINISHED_KEY]
            )

        datastore.Put(self)

    def run_shard(self, query, shard, operation, operation_method=None):
        if operation_method:
            operation = getattr(operation, operation_method)

        marker = datastore.Get(self.key())
        if cPickle.dumps(shard) not in marker[ShardedTaskMarker.RUNNING_KEY]:
            return

        query["__key__ >="] = shard[0]
        query["__key__ <"] = shard[1]
        query.Order("__key__")

        for entity in query.Run():
            operation(entity)

        # Once we've run the operation on all the entities, mark the shard as done
        def txn():
            pickled_shard = cPickle.dumps(shard)
            marker = datastore.Get(self.key())
            marker.__class__ = ShardedTaskMarker
            marker[ShardedTaskMarker.RUNNING_KEY].remove(pickled_shard)
            marker[ShardedTaskMarker.FINISHED_KEY].append(pickled_shard)
            marker.put()

        datastore.RunInTransaction(txn)

    def begin_processing(self, operation, operation_method):
        BATCH_SIZE = 3

        # Unpickle the source query
        query = cPickle.loads(str(self["query"]))

        def txn():
            try:
                marker = datastore.Get(self.key())
                marker.__class__ = ShardedTaskMarker

                queued_shards = marker[ShardedTaskMarker.QUEUED_KEY]
                processing_shards = marker[ShardedTaskMarker.RUNNING_KEY]
                queued_count = len(queued_shards)

                for j in xrange(min(BATCH_SIZE, queued_count)):
                    pickled_shard = queued_shards.pop()
                    processing_shards.append(pickled_shard)
                    shard = cPickle.loads(str(pickled_shard))
                    deferred.defer(
                        self.run_shard,
                        query,
                        shard,
                        operation,
                        operation_method,
                        _transactional=True
                    )

                marker.put()
            except datastore_errors.EntityNotFoundError:
                logging.error(
                    "Unable to start task %s as marker is missing",
                    self.key().id_or_name()
                )
                return

        # Reload the marker (non-transactionally) and defer the shards in batches
        # transactionally. If this task fails somewhere, it will resume where it left off
        marker = datastore.Get(self.key())
        for i in xrange(0, len(marker[ShardedTaskMarker.QUEUED_KEY]), BATCH_SIZE):
            datastore.RunInTransaction(txn)


def start_mapping(identifier, query, operation, operation_method=None):
    """ This must *transactionally* defer a task which will call `operation._wrapped_map_entity` on
        all entities of the given `kind` in the given `namespace` and will then transactionally
        update the entity of the given `task_marker_key_key` with `is_finished=True` after all
        entities have been mapped.
    """
    SHARD_COUNT = getattr(settings, "DJANGAE_MIGRATION_SHARD_COUNT", 32)

    @datastore.NonTransactional
    def calculate_shards():
        return shard_query(query, SHARD_COUNT)

    def txn():
        marker_key = ShardedTaskMarker.get_key(identifier, query._Query__namespace)
        try:
            datastore.Get(marker_key)
            raise ValueError("Task with this identifier already exists")
        except datastore_errors.EntityNotFoundError:
            pass

        marker = ShardedTaskMarker(identifier, query, namespace=query._Query__namespace)
        shards = calculate_shards()

        if shards:
            for shard in shards:
                marker["shards_queued"].append(cPickle.dumps(shard))
        else:
            # No shards, then there is nothing to do!
            marker["is_finished"] = True
        marker.put()
        if not marker["is_finished"]:
            deferred.defer(marker.begin_processing, operation, operation_method, _transactional=True)

        return marker_key

    return datastore.RunInTransaction(txn)


def is_mapper_running(identifier, namespace):
    try:
        marker = datastore.Get(ShardedTaskMarker.get_key(identifier, namespace))
        return not marker["is_finished"]
    except datastore_errors.EntityNotFoundError:
        return False
