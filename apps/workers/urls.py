from django.urls import path

from apps.workers.views import (
    EngagementAcceptView,
    EngagementDetailView,
    EngagementListView,
    EngagementRequestChangeView,
    LifecycleActivityUpdateView,
    WorkerContractExtendView,
    WorkerDirectoryDetailView,
    WorkerDirectoryListView,
    WorkerInviteView,
    WorkerLifecycleDetailView,
    WorkerLifecycleListView,
    WorkerOffboardingStartView,
)


urlpatterns = [
    path("workers", WorkerDirectoryListView.as_view(), name="worker-list"),
    path(
        "workers/<int:worker_id>",
        WorkerDirectoryDetailView.as_view(),
        name="worker-detail",
    ),
    path(
        "workers/<int:worker_id>/contract/extend",
        WorkerContractExtendView.as_view(),
        name="worker-contract-extend",
    ),
    path("engagements", EngagementListView.as_view(), name="engagement-list"),
    path(
        "engagements/<int:engagement_id>",
        EngagementDetailView.as_view(),
        name="engagement-detail",
    ),
    path(
        "engagements/<int:engagement_id>/accept",
        EngagementAcceptView.as_view(),
        name="engagement-accept",
    ),
    path(
        "engagements/<int:engagement_id>/request-change",
        EngagementRequestChangeView.as_view(),
        name="engagement-request-change",
    ),
    path(
        "workers/lifecycle",
        WorkerLifecycleListView.as_view(),
        name="worker-lifecycle-list",
    ),
    path(
        "workers/<int:worker_id>/lifecycle/<str:lifecycle_type>",
        WorkerLifecycleDetailView.as_view(),
        name="worker-lifecycle-detail",
    ),
    path(
        (
            "workers/<int:worker_id>/lifecycle/<str:lifecycle_type>/"
            "activities/<int:activity_id>"
        ),
        LifecycleActivityUpdateView.as_view(),
        name="worker-lifecycle-activity-update",
    ),
    path(
        "workers/<int:worker_id>/offboarding/start",
        WorkerOffboardingStartView.as_view(),
        name="worker-offboarding-start",
    ),
    path(
        "workers/<int:worker_id>/invite",
        WorkerInviteView.as_view(),
        name="worker-invite",
    ),
]
