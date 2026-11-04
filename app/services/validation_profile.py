from app.core.exceptions import ConflictError, NotFoundError
from app.models.validation_profile import ValidationProfile
from app.models.validation_rule import ValidationRule
from app.repositories.validation_profile import ValidationProfileRepository
from app.schemas.validation_profile import ValidationProfileCreate


class ValidationProfileService:
    def __init__(self, profile_repo: ValidationProfileRepository) -> None:
        self._profiles = profile_repo

    async def create_profile(self, data: ValidationProfileCreate) -> ValidationProfile:
        existing = await self._profiles.get_by_name(data.name)
        if existing:
            raise ConflictError("Validation profile with this name already exists")

        profile = ValidationProfile(
            name=data.name,
            description=data.description,
            document_type=data.document_type,
            is_active=True,
        )
        profile.rules = [
            ValidationRule(
                name=rule.name,
                description=rule.description,
                rule_type=rule.rule_type,
                field_name=rule.field_name,
                rule_config=rule.rule_config,
                severity=rule.severity,
                is_active=True,
                order=rule.order,
            )
            for rule in data.rules
        ]

        created = await self._profiles.create(profile)
        loaded = await self._profiles.get_with_rules(created.id)
        if not loaded:
            raise NotFoundError("Validation profile not found after creation")
        return loaded

    async def list_profiles(self) -> list[ValidationProfile]:
        return await self._profiles.list_with_rules()
