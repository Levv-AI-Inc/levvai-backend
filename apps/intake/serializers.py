from rest_framework import serializers

from apps.intake.models import IntakeQualification, IntakeRequest, IntakeSelectedCandidate


class IntakeQualificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakeQualification
        fields = [
            "id",
            "sequence",
            "name",
            "qualification_type",
            "group",
            "description",
            "mandatory",
            "knockout",
            "response_mode",
            "min_years",
            "proficiency",
            "weight",
            "tags",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        sequence = attrs.get("sequence", getattr(instance, "sequence", 1))
        name = (attrs.get("name", getattr(instance, "name", "")) or "").strip()
        min_years = attrs.get("min_years", getattr(instance, "min_years", 0))
        weight = attrs.get("weight", getattr(instance, "weight", 1))
        tags = attrs.get("tags", getattr(instance, "tags", []))

        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if not name:
            raise serializers.ValidationError({"name": "This field may not be blank."})
        if min_years < 0:
            raise serializers.ValidationError({"min_years": "Minimum years must be greater than or equal to 0."})
        if weight < 1 or weight > 5:
            raise serializers.ValidationError({"weight": "Weight must be between 1 and 5."})
        if tags is None:
            tags = []
        if not isinstance(tags, list):
            raise serializers.ValidationError({"tags": "Tags must be a list."})

        normalized_tags = []
        for tag in tags:
            text = str(tag).strip()
            if text:
                normalized_tags.append(text)

        attrs["name"] = name
        attrs["tags"] = normalized_tags
        return attrs


class IntakeRequestWriteSerializer(serializers.ModelSerializer):
    qualifications = IntakeQualificationSerializer(many=True, required=False)

    class Meta:
        model = IntakeRequest
        fields = [
            "engagement_type",
            "cost_center",
            "site",
            "supplier",
            "role_definition",
            "legal_entity",
            "title",
            "description",
            "start_date",
            "end_date",
            "worker_count",
            "target_rate",
            "rate_unit",
            "budget_amount",
            "currency",
            "country",
            "state_province",
            "city",
            "rate_card",
            "overtime_enabled",
            "overtime_multiplier",
            "custom_fields",
            "qualifications_enabled",
            "qualifications",
        ]

    def validate(self, attrs):
        qualifications = attrs.get("qualifications")
        if qualifications is None:
            qualifications = None

        if qualifications is not None:
            sequences = [item["sequence"] for item in qualifications]
            if len(sequences) != len(set(sequences)):
                raise serializers.ValidationError({"qualifications": "Qualification sequence values must be unique."})

        instance = getattr(self, "instance", None)

        country = attrs.get("country", getattr(instance, "country", ""))
        if country:
            country = country.strip().upper()
            if len(country) != 2:
                raise serializers.ValidationError({"country": "Country must be a 2-letter ISO 3166-1 alpha-2 code."})
            attrs["country"] = country

        currency = attrs.get("currency", getattr(instance, "currency", ""))
        if currency:
            currency = currency.strip().upper()
            if len(currency) != 3:
                raise serializers.ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})
            attrs["currency"] = currency

        state_province = attrs.get("state_province")
        if state_province is not None:
            attrs["state_province"] = state_province.strip()

        city = attrs.get("city")
        if city is not None:
            attrs["city"] = city.strip()

        overtime_enabled = attrs.get("overtime_enabled", getattr(instance, "overtime_enabled", False))
        overtime_multiplier = attrs.get("overtime_multiplier", getattr(instance, "overtime_multiplier", None))
        if overtime_enabled:
            if overtime_multiplier is None:
                raise serializers.ValidationError({"overtime_multiplier": "Overtime multiplier is required when overtime is enabled."})
            if overtime_multiplier <= 0:
                raise serializers.ValidationError({"overtime_multiplier": "Overtime multiplier must be greater than 0."})
        elif "overtime_multiplier" in attrs and overtime_multiplier is not None and overtime_multiplier <= 0:
            raise serializers.ValidationError({"overtime_multiplier": "Overtime multiplier must be greater than 0."})

        return attrs


class IntakeRequestDetailSerializer(serializers.ModelSerializer):
    qualifications = IntakeQualificationSerializer(many=True, read_only=True)

    class Meta:
        model = IntakeRequest
        fields = [
            "id",
            "tenant_id",
            "created_by",
            "created_at",
            "updated_at",
            "status",
            "approval_status",
            "engagement_type",
            "cost_center",
            "site",
            "supplier",
            "role_definition",
            "legal_entity",
            "title",
            "description",
            "start_date",
            "end_date",
            "worker_count",
            "target_rate",
            "rate_unit",
            "budget_amount",
            "currency",
            "country",
            "state_province",
            "city",
            "rate_card",
            "overtime_enabled",
            "overtime_multiplier",
            "custom_fields",
            "qualifications_enabled",
            "qualifications",
            "approval_chain",
            "approval_chain_snapshot",
            "approval_started_at",
            "submitted_at",
            "submitted_by",
            "decision_at",
            "decided_by",
            "decision_reason",
        ]


class IntakeDecisionSerializer(serializers.Serializer):
    decision_reason = serializers.CharField(required=False, allow_blank=True, default="")


class NovaConfidenceRequestSerializer(serializers.Serializer):
    intake_id = serializers.IntegerField()


class CandidateDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            IntakeSelectedCandidate.STATUS_REVIEWED,
            IntakeSelectedCandidate.STATUS_ACCEPTED,
            IntakeSelectedCandidate.STATUS_REJECTED,
        ]
    )


class IntakeSelectedCandidateSerializer(serializers.ModelSerializer):
    intake = serializers.PrimaryKeyRelatedField(read_only=True)
    supplier = serializers.PrimaryKeyRelatedField(read_only=True)
    submitted_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = IntakeSelectedCandidate
        fields = [
            "id",
            "intake",
            "supplier",
            "submitted_by",
            "full_name",
            "email",
            "phone",
            "notes",
            "resume_url",
            "available_start_date",
            "proposed_rate",
            "currency",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "intake",
            "supplier",
            "submitted_by",
            "status",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        full_name = (attrs.get("full_name") or "").strip()
        phone = (attrs.get("phone") or "").strip()
        notes = (attrs.get("notes") or "").strip()
        currency = (attrs.get("currency") or "").strip().upper()
        proposed_rate = attrs.get("proposed_rate")

        if not full_name:
            raise serializers.ValidationError({"full_name": "This field may not be blank."})
        if proposed_rate is not None and proposed_rate < 0:
            raise serializers.ValidationError({"proposed_rate": "Proposed rate must be greater than or equal to 0."})
        if currency and len(currency) != 3:
            raise serializers.ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

        attrs["full_name"] = full_name
        attrs["phone"] = phone
        attrs["notes"] = notes
        attrs["currency"] = currency
        return attrs


class CandidateDirectorySerializer(IntakeSelectedCandidateSerializer):
    job_posting_id = serializers.SerializerMethodField()
    intake_title = serializers.CharField(source="intake.title", read_only=True)
    role_name = serializers.SerializerMethodField()
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    hiring_manager_id = serializers.IntegerField(
        source="intake.created_by_id",
        read_only=True,
        allow_null=True,
    )
    hiring_manager_name = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    rate_unit = serializers.CharField(source="intake.rate_unit", read_only=True)
    skills = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    work_order_id = serializers.SerializerMethodField()
    work_order_number = serializers.SerializerMethodField()
    work_order_status = serializers.SerializerMethodField()

    class Meta(IntakeSelectedCandidateSerializer.Meta):
        fields = IntakeSelectedCandidateSerializer.Meta.fields + [
            "job_posting_id",
            "intake_title",
            "role_name",
            "supplier_name",
            "hiring_manager_id",
            "hiring_manager_name",
            "location",
            "rate_unit",
            "skills",
            "days_in_stage",
            "work_order_id",
            "work_order_number",
            "work_order_status",
        ]

    def get_job_posting_id(self, candidate):
        created_at = getattr(candidate.intake, "created_at", None)
        year = created_at.year if created_at else ""
        return f"JP-{year}-{candidate.intake_id:04d}" if year else f"JP-{candidate.intake_id:04d}"

    def get_role_name(self, candidate):
        role = getattr(candidate.intake, "role_definition", None)
        return getattr(role, "name", "") or candidate.intake.title

    def get_hiring_manager_name(self, candidate):
        manager = getattr(candidate.intake, "created_by", None)
        if not manager:
            return ""
        return manager.get_full_name().strip() or manager.username

    def get_location(self, candidate):
        intake = candidate.intake
        site = getattr(intake, "site", None)
        if site:
            return site.name

        parts = [intake.city, intake.state_province, intake.country]
        return ", ".join(part for part in parts if part)

    def get_skills(self, candidate):
        skills = []
        seen = set()
        for qualification in candidate.intake.qualifications.all():
            values = qualification.tags or [qualification.name]
            for value in values:
                label = str(value).strip()
                key = label.casefold()
                if label and key not in seen:
                    seen.add(key)
                    skills.append(label)
        return skills

    def get_days_in_stage(self, candidate):
        from django.utils import timezone

        changed_at = candidate.updated_at or candidate.created_at
        return max((timezone.now() - changed_at).days, 0)

    def get_work_order_id(self, candidate):
        work_order = self._latest_work_order(candidate)
        return work_order.id if work_order else None

    def get_work_order_number(self, candidate):
        work_order = self._latest_work_order(candidate)
        return work_order.work_order_number if work_order else ""

    def get_work_order_status(self, candidate):
        work_order = self._latest_work_order(candidate)
        return work_order.status if work_order else ""

    @staticmethod
    def _latest_work_order(candidate):
        prefetched = getattr(candidate, "candidate_work_orders", None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return candidate.work_orders.order_by("-created_at", "-id").first()
