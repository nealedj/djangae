from django.utils.encoding import is_protected_type


def handle_fk_field_patch(self, obj, field):
    """
    Patches the handle_fk_field() method in order to make sure that the field's
    `value_to_string()` is called.

    This is fixed in Django 1.8.
    """
    if self.use_natural_foreign_keys and hasattr(field.rel.to, 'natural_key'):
        related = getattr(obj, field.name)
        if related:
            value = related.natural_key()
        else:
            value = None
    else:
        value = getattr(obj, field.get_attname())
        if not is_protected_type(value):
            value = field.value_to_string(obj)
    self._current[field.name] = value


def patch():
    from django.core.serializers.python import Serializer as OriginalSerializer

    OriginalSerializer.handle_fk_field = handle_fk_field_patch
