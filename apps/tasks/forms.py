from django import forms

from apps.crm.models import Company, Lead

from .models import Project, Task


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "description", "assignee", "due_date", "priority", "status", "project", "company", "lead"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "What needs doing", "autofocus": True, "maxlength": 200}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Details, links, context (optional)"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "lead": forms.HiddenInput(),
        }

    def __init__(self, *args, users, projects, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = self.fields["assignee"].queryset.filter(pk__in=[u.pk for u in users])
        self.fields["assignee"].empty_label = "Unassigned"
        self.fields["project"].queryset = projects
        self.fields["project"].empty_label = "No project"
        self.fields["company"].queryset = Company.objects.order_by("name")[:500]
        self.fields["company"].empty_label = "No company"
        self.fields["lead"].queryset = Lead.objects.all()
        self.fields["lead"].required = False


class QuickTaskForm(forms.Form):
    title = forms.CharField(max_length=200)
    due_date = forms.DateField(required=False)
    assignee = forms.IntegerField(required=False)
    project = forms.IntegerField(required=False)


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "company", "owner", "members", "status", "color", "start_date", "due_date"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Anand Pharma onboarding", "autofocus": True}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Goal, scope, anything the team should know"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "members": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, users, **kwargs):
        super().__init__(*args, **kwargs)
        ids = [u.pk for u in users]
        self.fields["owner"].queryset = self.fields["owner"].queryset.filter(pk__in=ids)
        self.fields["owner"].empty_label = "— pick —"
        self.fields["members"].queryset = self.fields["members"].queryset.filter(pk__in=ids).order_by("name")
        self.fields["company"].queryset = Company.objects.order_by("name")[:500]
        self.fields["company"].empty_label = "No company"

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("due_date") and d["due_date"] < d["start_date"]:
            self.add_error("due_date", "Due date is before the start date.")
        return d
