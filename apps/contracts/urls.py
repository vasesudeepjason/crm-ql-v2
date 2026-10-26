from django.urls import path

from . import views

app_name = "contracts"
urlpatterns = [
    path("", views.index, name="index"),
    path("renewals/", views.renewals, name="renewals"),
    path("new/", views.form, name="new"),
    path("contacts/<int:company_id>/", views.contacts_for, name="contacts_for"),
    path("catalogue/", views.products, name="products"),
    path("catalogue/new/", views.product_form, name="product_new"),
    path("catalogue/<int:pk>/edit/", views.product_form, name="product_edit"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.form, name="edit"),
    path("<int:pk>/renew/", views.renew, name="renew"),
    path("<int:pk>/lapse/", views.lapse, name="lapse"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
]
