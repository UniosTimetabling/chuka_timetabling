from django.db import models
from django.utils import timezone
class Food(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def first_image(self):
        return self.images.first().image.url if self.images.exists() else None


class FoodImage(models.Model):
    food = models.ForeignKey(Food, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(upload_to="foods/")


class FoodOption(models.Model):
    food = models.ForeignKey(Food, related_name="options", on_delete=models.CASCADE)
    option = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.food.name} - {self.option}"

class Offer(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    image = models.ImageField(upload_to="offers/", null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)  # ✅ set default to now

    def __str__(self):
        return self.title