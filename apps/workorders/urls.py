from django.urls import path

from apps.workorders.views import (
    WorkOrderApproveView,
    WorkOrderDetailView,
    WorkOrderListCreateView,
    WorkOrderRejectView,
    WorkOrderSubmitView,
)

urlpatterns = [
    path("work-orders", WorkOrderListCreateView.as_view(), name="work-order-list-create"),
    path("work-orders/<int:work_order_id>", WorkOrderDetailView.as_view(), name="work-order-detail"),
    path("work-orders/<int:work_order_id>/submit", WorkOrderSubmitView.as_view(), name="work-order-submit"),
    path("work-orders/<int:work_order_id>/approve", WorkOrderApproveView.as_view(), name="work-order-approve"),
    path("work-orders/<int:work_order_id>/reject", WorkOrderRejectView.as_view(), name="work-order-reject"),
]
