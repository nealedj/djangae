from distutils.version import StrictVersion
import django
from django.conf import settings


def patch():
    if 'django.contrib.contenttypes' in settings.INSTALLED_APPS:
        from . import contenttypes
        contenttypes.patch()

    if StrictVersion(django.get_version()) < StrictVersion('1.8'):
        from . import serialization
        serialization.patch()
