# Djangae Contrib Search

A contrib app which provides to integrate with the App Engine Search API.

## Dependencies

The contrib app depends on a separate search library.

`pip install git+https://github.com/potatolondon/search.git`

## Basic usage

### Define a search document

```python

from search import fields, indexers
from djangae.contrib.search.documents import Document


class FooDocument(Document):
	name = fields.TextField()

	def build(self, instance):
		self.name = instance.name

	def build_corpus(self):
		terms = indexers.startswith(self.name)
		return u" ".join(terms)
```

`build()` defines how to construct the document from the source Django model.

`build_corpus()` is optional and allows you to build up a single field with terms from many other fields.

### Hook the document up to a Django model

```python
from django.db import models
from djangae.contrib.search.decorators import searchable

@searchable("myapp.documents.FooDocument")
class FooModel(models.Model):
    name = models.CharField(max_length=256)
```

This sets up signals to keep the document in the search index up to date with the model in the datastore.

### Index all documents

```python
from djangae.contrib.search.tasks import ReindexMapReduceTask, get_models_for_actions

model_cls, doc_cls = get_models_for_actions("myapp", "FooModel")
mr_task = ReindexMapReduceTask(model_cls)
mr_task.start()
```

### Query

```
from djangae.contrib.utils import django_qs_to_search_qs

from myapp.models import FooModel

django_queryset = FooModel.objects.filter(corpus__contains="bar")
search_queryset = django_qs_to_search_qs(django_queryset)

for django_model in search_queryset.as_model_objects():
    # gets the model IDs from the search API and then queries the datastore to fetch the models
    print(django_model.name)
```
