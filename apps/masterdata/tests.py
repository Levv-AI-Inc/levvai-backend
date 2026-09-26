from unittest.mock import patch

from django.test import SimpleTestCase

from apps.masterdata.models import RoleDefinition
from apps.masterdata.serializers import RoleDefinitionSerializer


class RoleDefinitionSerializerTests(SimpleTestCase):
    def test_partial_update_validates_as_the_existing_database_row(self):
        role = RoleDefinition(
            id=3,
            code="software-engineer-2-ca-on-toronto",
            name="Software Engineer 2",
            description="Software Engineering role for the Toronto market",
            country="CA",
            region="ON",
            city="Toronto",
            default_currency="CAD",
            default_unit=RoleDefinition.UNIT_HOUR,
            is_active=True,
        )
        role._state.adding = False
        role._state.db = "default"

        serializer = RoleDefinitionSerializer(
            role,
            data={"city": "New York"},
            partial=True,
        )
        # Isolate the serializer's model-validation path from DRF's separate
        # database-backed UniqueTogetherValidator.
        serializer.validators = []

        with (
            patch.object(RoleDefinition, "validate_unique", autospec=True) as validate_unique,
            patch.object(RoleDefinition, "validate_constraints", autospec=True) as validate_constraints,
        ):
            self.assertTrue(serializer.is_valid(), serializer.errors)

        validated_role = validate_unique.call_args.args[0]
        self.assertEqual(validated_role.pk, role.pk)
        self.assertEqual(validated_role.code, role.code)
        self.assertEqual(validated_role.city, "New York")
        self.assertFalse(validated_role._state.adding)
        self.assertEqual(validated_role._state.db, role._state.db)
        self.assertIs(validate_constraints.call_args.args[0], validated_role)
