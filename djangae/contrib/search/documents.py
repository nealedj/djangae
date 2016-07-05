import itertools
from django.core import exceptions
from django.db import models
from django.conf import settings
from djangae import fields as djangae_fields

from search import fields, indexes


class Document(indexes.DocumentModel):
    """Base document class for all SOC documents. Supplies `pk` and `corpus`
    fields as standard, as well as method hooks allowing customization of how
    instance values get copied to the search document.
    """
    pk = fields.TextField()
    program = fields.TextField()
    corpus = fields.TextField()

    def build_base(self, instance):
        """Called by the model's post_save signal receiver when indexing an
        instance of that model.

        Args:
            instance: A Django model instance
        """
        self.pk = str(instance.pk)
        self.program = str(getattr(instance, "program_id", None))
        self.build(instance)
        self.corpus = self.build_corpus()

    def build(self, instance):
        raise NotImplementedError()

    def build_corpus(self):
        """Build the value for the document's corpus field. This is usually the
        field used for keyword searching.
        """
        # Doesn't raise `NotImplemented` because the child class might not care
        return ""


class DocumentOptions(object):
    """Container class for meta options defined in a Django model's SearchMeta subclass
    """
    def __init__(self, meta):
        self.field_names = getattr(meta, 'fields', [])
        self.field_types = getattr(meta, 'field_types', {})
        self.field_mappers = getattr(meta, 'field_mappers', {})
        self.corpus = getattr(meta, 'corpus', {})
        self.fields = {}


class DynamicDocumentFactory(object):
    """Used to create a class inheriting from DynamicDocument with fields defined
    in a SearchMeta subclass
    """
    django_type_map = {
        models.AutoField: fields.IntegerField,
        models.BigIntegerField: fields.IntegerField,
        models.BooleanField: fields.BooleanField,
        models.CharField: fields.TextField,
        models.CommaSeparatedIntegerField: fields.TextField,
        models.DateField: fields.DateField,
        models.DecimalField: fields.FloatField,
        models.EmailField: fields.TextField,
        models.FloatField: fields.FloatField,
        models.IntegerField: fields.IntegerField,
        models.NullBooleanField: fields.BooleanField,
        models.PositiveIntegerField: fields.IntegerField,
        models.PositiveSmallIntegerField: fields.IntegerField,
        models.SlugField: fields.TextField,
        models.SmallIntegerField: fields.IntegerField,
        models.TextField: fields.TextField,
        models.URLField: fields.TextField,

        # assume that list fields will probably need to map to a plain
        # text field
        djangae_fields.ListField: fields.TextField,
        djangae_fields.SetField: fields.TextField
    }

    def __init__(self, model_class):
        search_meta = getattr(model_class, 'SearchMeta', None)

        if not search_meta:
            raise Exception(
                u'Cannot make {model_class} searchable. Must either pass a search '
                'document class or define a SearchMeta class as an attribute of '
                'the model class.'.format(model_class=model_class)
            )

        self.meta = DocumentOptions(search_meta)
        self.model_class = model_class

    def create(self):
        document_class = type(
            '{model_class.__name__}Document'.format(model_class=self.model_class),
            (DynamicDocument,),
            {'_doc_meta': self.meta}
        )
        self.build_fields(document_class)
        return document_class

    def build_fields(self, new_cls):
        for field_name in self.meta.field_names:
            field = self.get_field(field_name)

            field.add_to_class(new_cls, field_name)
            self.meta.fields[field_name] = field

    def get_field(self, field_name):
        try:
            django_field = self.model_class._meta.get_field(field_name)
        except exceptions.FieldDoesNotExist:
            if field_name not in self.meta.field_mappers:
                    raise Exception(
                    u'{field_name} is not a field on the Django model and '
                    'does not have a field_mapper defined for it.'
                    .format(field_name=field_name)
                )

        if field_name in self.meta.field_types:
            field = self.meta.field_types[field_name]
            if callable(field):
                field = field()

        else:
            if isinstance(django_field, models.DateTimeField):
                field_cls = self.get_datetime_field()
            else:
                field_cls = self.django_type_map.get(django_field.__class__)

            if not field_cls:
                raise Exception(
                    u'{field_name} of type {django_field.__class__} '
                    'cannot be automatically mapped to a search field type. '
                    'Try explicitly defining the field in SearchMeta.field_types'
                    .format(field_name=field_name, django_field=django_field)
                )

            field = field_cls()

        return field

    @staticmethod
    def get_datetime_field():
        return (
            fields.TZDateTimeField
            if settings.USE_TZ
            else fields.DateTimeField
        )


class DynamicDocument(Document):
    """Document class from which dynamically created documents inherit. These documents are defined using
    a SearchMeta subclass on a DjangoModel decorated by @searchable.
    """
    def build(self, instance):
        for field_name in self._doc_meta.fields.keys():

            value = self.map_field_value(instance, field_name)

            setattr(self, field_name, value)

    def map_field_value(self, instance, field_name):
        meta = self._doc_meta

        if field_name in meta.field_mappers:
            value = meta.field_mappers[field_name](instance)
        else:
            value = getattr(instance, field_name)

            if isinstance(value, (list, set,)):
                value = u" ".join(value)

        return value

    def build_corpus(self):
        corpus_meta = self._doc_meta.corpus

        if not corpus_meta:
            return ''

        indexed = [
            index_fn(getattr(self, field_name))
            for field_name, index_fn in
            corpus_meta.items()
        ]

        indexed = set(itertools.chain.from_iterable(indexed))
        return " ".join(indexed)
