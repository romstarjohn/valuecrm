from django.urls import path
from . import views

app_name = "operations"

urlpatterns = [
    path("", views.hub, name="hub"),

    path("orders/", views.order_list, name="order_list"),
    path("orders/<uuid:reference>/", views.order_detail, name="order_detail"),
    path("orders/<uuid:reference>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<uuid:reference>/disposition/", views.order_disposition, name="order_disposition"),
    path("orders/<uuid:reference>/freeze-enrollment/", views.order_freeze_enrollment, name="order_freeze_enrollment"),
    path("orders/<uuid:reference>/resume-enrollment/", views.order_resume_enrollment, name="order_resume_enrollment"),

    path("installments/", views.installment_list, name="installment_list"),
    path("installments/<int:pk>/cancel/", views.installment_cancel, name="installment_cancel"),
    path("installments/<int:pk>/waive/", views.installment_waive, name="installment_waive"),

    path("payment-attempts/", views.payment_attempt_list, name="payment_attempt_list"),
    path("payment-attempts/<int:pk>/check-status/", views.payment_attempt_check_status, name="payment_attempt_check_status"),

    path("webhook-events/", views.webhook_event_list, name="webhook_event_list"),
    path("webhook-events/<int:pk>/attribute/", views.webhook_event_attribute, name="webhook_event_attribute"),

    path("confirmations/", views.confirmation_list, name="confirmation_list"),
    path("confirmations/<int:pk>/retry/", views.confirmation_retry, name="confirmation_retry"),

    path("provisioning/", views.provisioning_list, name="provisioning_list"),
    path("provisioning/<int:pk>/retry/", views.provisioning_retry, name="provisioning_retry"),

    path("reconciliation/", views.reconciliation_list, name="reconciliation_list"),

    path("audit/", views.audit_list, name="audit_list"),
]
