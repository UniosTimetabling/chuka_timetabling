from django.db import models


class ImportCorrectionLog(models.Model):
    """
    Stage 11 – Learning & Adaptation.

    Every time a user corrects a field value during Smart Importer 2.0 review,
    the correction is stored here. Future import runs automatically apply these
    learned corrections via the Correction Engine (Stage 6) before human review,
    reducing the number of manual fixes over time.

    Example row:
        field="department", original_value="comp sci", corrected_value="Computer Science"
    """
    field = models.CharField(max_length=100, db_index=True)
    original_value = models.CharField(max_length=500)
    corrected_value = models.CharField(max_length=500)
    applied_count = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("field", "original_value")
        ordering = ("field", "original_value")
        verbose_name = "Import Correction"
        verbose_name_plural = "Import Corrections"

    def __str__(self):
        return f"[{self.field}] '{self.original_value}' → '{self.corrected_value}'"

