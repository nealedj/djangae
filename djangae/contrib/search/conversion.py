import json
import random

from django.db.models.fields import (
    DateField,
    DateTimeField
)
from django.db.models.fields.related import (
    ForeignKey,
    OneToOneField,
)
from django.db.models.options import FieldDoesNotExist
from django.utils.timezone import make_naive, make_aware, is_naive

from djangae.fields import (
    ListField,
    SetField,
    RelatedSetField,
    RelatedListField,
    JSONField,
)

from search import fields


def get_random_rank():
    return random.randint(0, int(1e6))


def to_search_api_datetime(d):
    return make_naive(d) if d else d


noop = lambda x: x


FIELD_COERCE_MAP = {
    fields.TextField: unicode,
    fields.HtmlField: unicode,
    fields.IntegerField: int,
    fields.FloatField: float,
    fields.DateField: to_search_api_datetime,
    fields.BooleanField: bool,
    fields.AtomField: unicode,
    fields.GeoField: unicode
}


def get_value_for_model_field(obj, field):
    """Get a primitive value for `field` on models instance `obj`"""
    field_class = type(field)

    if field_class in (ForeignKey, OneToOneField):
        return getattr(obj, field.attname)
    elif field_class in (ListField, SetField, RelatedSetField, RelatedListField):
        return u"|".join(map(unicode, list(getattr(obj, field.attname))))
    elif field_class == JSONField:
        return json.dumps(getattr(obj, field.attname))
    elif field.rel:
        raise NotImplementedError(u"Relation %s not supported" % field.relation)
    else:
        return getattr(obj, field.attname)


def get_value_for_doc_field(obj, field, value):
    """Convert a search document field value to one matching it's Django
    model field.
    """
    if value is None:
        return value

    field_class = type(field)

    if field_class in (DateField, DateTimeField):
        if is_naive(value):
            value = make_aware(value)
        return value
    elif field_class in (RelatedSetField, RelatedListField):
        if value:
            value = [field.rel.to._meta.pk.to_python(x) for x in value.split("|")]
        return value if value else []
    elif field_class in (SetField,):
        if value:
            value = {x for x in value.split("|")}
        return value if value else set()
    elif field_class in (ListField,):
        if value:
            value = [x for x in value.split("|")]
        return value if value else []
    elif field_class in (ForeignKey, ):
        return int(value)
    else:
        return field.to_python(value)


def build_id(model, pk):
    return str(pk)


def model_to_document(obj, doc):
    """Map any common fields from the obj to the doc."""

    for name, doc_field in doc._meta.fields.iteritems():

        coerce_func = FIELD_COERCE_MAP.get(type(doc_field), noop)

        # If custom getter is defined we call this with the model obj
        # and store the results on the doc field.
        getter = getattr(doc, 'build_%s' % name, None)
        if getter:
            if getter(obj) is not None:
                setattr(doc, name, coerce_func(getter(obj)))
            else:
                setattr(doc, name, None)
        else:
            try:
                model_field = obj._meta.get_field(name)
                model_value = get_value_for_model_field(obj, model_field)
                if model_value is not None:
                    setattr(doc, name, coerce_func(model_value))
            except FieldDoesNotExist:
                pass

    if hasattr(doc, 'model_to_document'):
        doc.model_to_document(doc, obj)


def document_to_model(doc, obj):
    """Map any common fields from the doc to the model obj."""
    for model_field in obj._meta.fields:
        if model_field.name in doc._meta.fields:
            name = model_field.name
            doc_value = getattr(doc, name)
            value = get_value_for_doc_field(obj, model_field, doc_value)

            # quick & ugly hack to handle cases where `value=['None', '2013', 'None', '2012']`
            # which causes a `ValidationError` for fields that expect only integers
            if isinstance(value, (list, tuple)):
                value = [
                    None if (
                        isinstance(v, basestring) and
                        v.lower() == 'none')
                    else v
                    for v in value
                ]

            setattr(obj, model_field.attname, value)

    if hasattr(doc, 'document_to_model'):
        doc.document_to_model(doc, obj)


def from_instance(doc_cls, obj):
    """Build a document instance from a model instance."""
    doc = doc_cls(
        doc_id=build_id(obj._meta.model, obj.pk),
        pk=str(obj.pk),
        _rank=get_random_rank()
    )
    model_to_document(obj, doc)
    return doc


def to_instance(doc, model_cls):
    """Build a model instance from a document instance."""
    obj = model_cls(pk=int(doc.pk))
    document_to_model(doc, obj)
    return obj
