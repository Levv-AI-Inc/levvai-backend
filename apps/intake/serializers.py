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


class NovaChatMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["system", "user", "assistant"], required=False, default="user")
    content = serializers.CharField(allow_blank=False, trim_whitespace=True)


class NovaChatRequestSerializer(serializers.Serializer):
    messages = NovaChatMessageSerializer(many=True, allow_empty=False)
    policyActive = serializers.BooleanField(required=False, default=False)


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
