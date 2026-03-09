from rest_framework import serializers


class SupplierRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    supplier_id = serializers.IntegerField(min_value=1, required=False)
    invite_token = serializers.CharField(required=False, allow_blank=False, max_length=128)

    def validate(self, attrs):
        if not attrs.get("supplier_id") and not attrs.get("invite_token"):
            raise serializers.ValidationError("Either supplier_id or invite_token is required.")
        return attrs


class SupplierLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class UserRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
