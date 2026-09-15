from django import forms
from django.contrib.auth.models import User, Group
from faculty_management.models import Faculty
from department_management.models import Department
from course_allocation.models import CourseAllocation
from timetable.models import Timetable 
from timetable.models import SchedulerConfig
from core.models import CotUserProfile
class FacultyForm(forms.ModelForm):
    class Meta:
        model = Faculty
        fields = ["name", "leader"]

class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "faculty", "leader"]


class UserForm(forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "email", "is_active", "is_staff", "is_superuser", "password"]

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email:
            qs = User.objects.filter(email__iexact=email)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "A user with this email address already exists."
                )
        return email

class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name"]


from django import forms


class CourseAllocationForm(forms.ModelForm):
    class Meta:
        model = CourseAllocation
        fields = [
            "course_code",
            "course_name",
            "lecturer",
            "department",
            "origin_department",   # user will select this manually
            "number_of_students",
            "approved_by_dvc",
            "rejected_by_dvc",
        ]
        widgets = {
            "course_code": forms.TextInput(attrs={"class": "form-control"}),
            "course_name": forms.TextInput(attrs={"class": "form-control"}),
            "lecturer": forms.Select(attrs={"class": "form-control"}),
            "department": forms.Select(attrs={"class": "form-control"}),
            "origin_department": forms.Select(attrs={
                "class": "form-control",
                "title": "Select the department offering this course",
            }),
            "number_of_students": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
                "placeholder": "Enter number of students"
            }),
            "approved_by_dvc": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "rejected_by_dvc": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove any automatic setting so user can pick freely
        self.fields["origin_department"].required = True




class TimetableForm(forms.ModelForm):
    class Meta:
        model = Timetable
        fields = ["course_allocation", "venue", "day", "start_time", "end_time"]

class SchedulerConfigForm(forms.ModelForm):
    class Meta:
        model = SchedulerConfig
        fields = [
            # Regular session
            "start_time",
            "end_time",
            "slot_size",
            # Evening classes
            "enable_evening_classes",
            "evening_start_time",
            "evening_end_time",
            "evening_slot_count",
            # Weekend classes
            "enable_weekend_classes",
            "weekend_start_time",
            "weekend_end_time",
            "weekend_slot_size",
        ]
        help_texts = {
            "start_time": "Regular weekday session start (24-hour, e.g. 07:00).",
            "end_time": "Regular weekday session end (24-hour, e.g. 19:00).",
            "slot_size": "Duration of each regular time-slot in hours.",
            "enable_evening_classes": "Schedule courses flagged Evening/Weekend into weekday evening slots. Regular courses are never placed here.",
            "evening_start_time": "Evening session start, default 19:00 (7 PM).",
            "evening_end_time": "Evening session end, default 21:00 (9 PM).",
            "evening_slot_count": (
                "How many evening slots are available per weekday. "
                "Default 1 gives a single 7-9 PM block."
            ),
            "enable_weekend_classes": "Schedule courses flagged Evening/Weekend into Saturday/Sunday slots. Regular courses are never placed here.",
            "weekend_start_time": "Weekend session start, default 09:00 (9 AM).",
            "weekend_end_time": "Weekend session end, default 17:00 (5 PM).",
            "weekend_slot_size": "Slot duration in hours for weekend sessions (default 3).",
        }




class CotUserForm(forms.ModelForm):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput, required=False)  # optional for editing
    department = forms.ModelChoiceField(queryset=Department.objects.all())

    class Meta:
        model = User
        fields = ["username", "email", "password", "department"]

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email:
            # When editing, exclude current user
            qs = User.objects.filter(email__iexact=email)
            user_instance = self.initial.get("_user_instance")
            if user_instance and user_instance.pk:
                qs = qs.exclude(pk=user_instance.pk)
            if qs.exists():
                raise forms.ValidationError("A user with this email address already exists.")
        return email

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if username:
            user_instance = self.initial.get("_user_instance")
            qs = User.objects.filter(username=username)
            if user_instance and user_instance.pk:
                qs = qs.exclude(pk=user_instance.pk)
            if qs.exists():
                raise forms.ValidationError("A user with this username already exists.")
        return username

    def save(self, commit=True, user_instance=None):
        if user_instance:  
            # 🔄 Update existing user
            user = user_instance
            user.username = self.cleaned_data["username"]
            user.email = self.cleaned_data["email"]
            if self.cleaned_data["password"]:
                user.set_password(self.cleaned_data["password"])
            if commit:
                user.save()

            # update COT profile
            profile, _ = CotUserProfile.objects.get_or_create(user=user)
            profile.department = self.cleaned_data["department"]
            profile.save()

        else:
            # ➕ Create new user
            user = User(
                username=self.cleaned_data["username"],
                email=self.cleaned_data["email"]
            )
            user.set_password(self.cleaned_data["password"])
            if commit:
                user.save()

            CotUserProfile.objects.create(
                user=user,
                department=self.cleaned_data["department"]
            )

        # Assign to COT group
        from django.contrib.auth.models import Group
        cot_group, _ = Group.objects.get_or_create(name="COT")
        user.groups.add(cot_group)

        return user