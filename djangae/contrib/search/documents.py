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
