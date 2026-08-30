from django.urls import path

from apps.timesheets.views import (
    TenantTimesheetApproveView,
    TenantTimesheetDetailView,
    TenantTimesheetListView,
    TenantTimesheetRejectView,
    WorkerTimesheetContextView,
    WorkerTimesheetCostAllocationView,
    WorkerTimesheetDetailView,
    WorkerTimesheetListCreateView,
    WorkerTimesheetSubmitView,
)

urlpatterns = [
    path("worker/timesheet-context", WorkerTimesheetContextView.as_view(), name="worker-timesheet-context"),
    path("worker/timesheet-cost-allocation", WorkerTimesheetCostAllocationView.as_view(), name="worker-timesheet-cost-allocation"),
    path("worker/timesheets", WorkerTimesheetListCreateView.as_view(), name="worker-timesheet-list-create"),
    path("worker/timesheets/<int:timesheet_id>", WorkerTimesheetDetailView.as_view(), name="worker-timesheet-detail"),
    path("worker/timesheets/<int:timesheet_id>/submit", WorkerTimesheetSubmitView.as_view(), name="worker-timesheet-submit"),
    path("timesheets", TenantTimesheetListView.as_view(), name="tenant-timesheet-list"),
    path("timesheets/<int:timesheet_id>", TenantTimesheetDetailView.as_view(), name="tenant-timesheet-detail"),
    path("timesheets/<int:timesheet_id>/approve", TenantTimesheetApproveView.as_view(), name="tenant-timesheet-approve"),
    path("timesheets/<int:timesheet_id>/reject", TenantTimesheetRejectView.as_view(), name="tenant-timesheet-reject"),
]
