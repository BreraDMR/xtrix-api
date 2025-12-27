"""Enums and constants for specialist types and categories."""
from enum import Enum


class SpecialistCategory(str, Enum):
    """Main category of specialist."""
    BEAUTY = "beauty"
    WELLNESS = "wellness"
    HEALTHCARE = "healthcare"


class SpecialistType(str, Enum):
    """Specific type of specialist within a category."""
    # Beauty category
    MASSAGE = "massage"  # Массажист / массажистка
    COSMETOLOGIST = "cosmetologist"  # Косметолог
    BARBER = "barber"  # Барбер / парикмахер
    HAIRDRESSER = "hairdresser"  # Парикмахер
    
    # Wellness category
    PHYSIOTHERAPIST = "physiotherapist"  # Физиотерапевт
    NUTRITIONIST = "nutritionist"  # Нутрициолог
    
    # Healthcare category
    THERAPIST = "therapist"  # Терапевт
    DENTIST = "dentist"  # Стоматолог
    DERMATOLOGIST = "dermatologist"  # Дерматолог
    DOCTOR = "doctor"  # Врач-специалист
    
    # Additional types (can be extended)
    ESTHETICIAN = "esthetician"  # Эстетист
    NAIL_TECHNICIAN = "nail_technician"  # Маникюрщик / педикюрщик
    LASH_TECHNICIAN = "lash_technician"  # Мастер по ресницам / бровям
    MAKEUP_ARTIST = "makeup_artist"  # Визажист
    AROMATHERAPIST = "aromatherapist"  # Ароматерапевт
    FITNESS_COACH = "fitness_coach"  # Фитнес-тренер
    YOGA_INSTRUCTOR = "yoga_instructor"  # Инструктор йоги
    PSYCHOLOGIST = "psychologist"  # Психолог
    PSYCHOTHERAPIST = "psychotherapist"  # Психотерапевт
    CARDIOLOGIST = "cardiologist"  # Кардиолог
    NEUROLOGIST = "neurologist"  # Невролог
    ORTHOPEDIST = "orthopedist"  # Ортопед
    GYNECOLOGIST = "gynecologist"  # Гинеколог
    UROLOGIST = "urologist"  # Уролог
    OPHTHALMOLOGIST = "ophthalmologist"  # Офтальмолог
    ENDOCRINOLOGIST = "endocrinologist"  # Эндокринолог
    PEDIATRICIAN = "pediatrician"  # Педиатр
    SURGEON = "surgeon"  # Хирург
    ONCOLOGIST = "oncologist"  # Онколог


# Mapping of specialist types to their categories
SPECIALIST_TYPE_TO_CATEGORY = {
    # Beauty
    SpecialistType.MASSAGE: SpecialistCategory.BEAUTY,
    SpecialistType.COSMETOLOGIST: SpecialistCategory.BEAUTY,
    SpecialistType.BARBER: SpecialistCategory.BEAUTY,
    SpecialistType.HAIRDRESSER: SpecialistCategory.BEAUTY,
    SpecialistType.ESTHETICIAN: SpecialistCategory.BEAUTY,
    SpecialistType.NAIL_TECHNICIAN: SpecialistCategory.BEAUTY,
    SpecialistType.LASH_TECHNICIAN: SpecialistCategory.BEAUTY,
    SpecialistType.MAKEUP_ARTIST: SpecialistCategory.BEAUTY,
    
    # Wellness
    SpecialistType.PHYSIOTHERAPIST: SpecialistCategory.WELLNESS,
    SpecialistType.NUTRITIONIST: SpecialistCategory.WELLNESS,
    SpecialistType.AROMATHERAPIST: SpecialistCategory.WELLNESS,
    SpecialistType.FITNESS_COACH: SpecialistCategory.WELLNESS,
    SpecialistType.YOGA_INSTRUCTOR: SpecialistCategory.WELLNESS,
    SpecialistType.PSYCHOLOGIST: SpecialistCategory.WELLNESS,
    SpecialistType.PSYCHOTHERAPIST: SpecialistCategory.WELLNESS,
    
    # Healthcare
    SpecialistType.THERAPIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.DENTIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.DERMATOLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.DOCTOR: SpecialistCategory.HEALTHCARE,
    SpecialistType.CARDIOLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.NEUROLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.ORTHOPEDIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.GYNECOLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.UROLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.OPHTHALMOLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.ENDOCRINOLOGIST: SpecialistCategory.HEALTHCARE,
    SpecialistType.PEDIATRICIAN: SpecialistCategory.HEALTHCARE,
    SpecialistType.SURGEON: SpecialistCategory.HEALTHCARE,
    SpecialistType.ONCOLOGIST: SpecialistCategory.HEALTHCARE,
}


def get_category_for_type(specialist_type: SpecialistType) -> SpecialistCategory:
    """Get category for a given specialist type."""
    return SPECIALIST_TYPE_TO_CATEGORY.get(specialist_type, SpecialistCategory.BEAUTY)


# Required fields for different categories
# This can be used for validation
REQUIRED_FIELDS_BY_CATEGORY = {
    SpecialistCategory.BEAUTY: [],  # Beauty doesn't require special medical fields
    SpecialistCategory.WELLNESS: [],  # Wellness is optional
    SpecialistCategory.HEALTHCARE: [
        "allergies",  # Аллергии - обязательно для медицины
        "chronic_diseases",  # Хронические заболевания - обязательно для медицины
    ],
}







