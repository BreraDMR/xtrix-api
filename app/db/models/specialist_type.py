from enum import Enum
class SpecialistCategory(str, Enum):
    BEAUTY = "beauty"
    WELLNESS = "wellness"
    HEALTHCARE = "healthcare"
class SpecialistType(str, Enum):
    MASSAGE = "massage"  
    COSMETOLOGIST = "cosmetologist"  
    BARBER = "barber"  
    HAIRDRESSER = "hairdresser"  
    PHYSIOTHERAPIST = "physiotherapist"  
    NUTRITIONIST = "nutritionist"  
    THERAPIST = "therapist"  
    DENTIST = "dentist"  
    DERMATOLOGIST = "dermatologist"  
    DOCTOR = "doctor"  
    ESTHETICIAN = "esthetician"  
    NAIL_TECHNICIAN = "nail_technician"  
    LASH_TECHNICIAN = "lash_technician"  
    MAKEUP_ARTIST = "makeup_artist"  
    AROMATHERAPIST = "aromatherapist"  
    FITNESS_COACH = "fitness_coach"  
    YOGA_INSTRUCTOR = "yoga_instructor"  
    PSYCHOLOGIST = "psychologist"  
    PSYCHOTHERAPIST = "psychotherapist"  
    CARDIOLOGIST = "cardiologist"  
    NEUROLOGIST = "neurologist"  
    ORTHOPEDIST = "orthopedist"  
    GYNECOLOGIST = "gynecologist"  
    UROLOGIST = "urologist"  
    OPHTHALMOLOGIST = "ophthalmologist"  
    ENDOCRINOLOGIST = "endocrinologist"  
    PEDIATRICIAN = "pediatrician"  
    SURGEON = "surgeon"  
    ONCOLOGIST = "oncologist"  
SPECIALIST_TYPE_TO_CATEGORY = {
    SpecialistType.MASSAGE: SpecialistCategory.BEAUTY,
    SpecialistType.COSMETOLOGIST: SpecialistCategory.BEAUTY,
    SpecialistType.BARBER: SpecialistCategory.BEAUTY,
    SpecialistType.HAIRDRESSER: SpecialistCategory.BEAUTY,
    SpecialistType.ESTHETICIAN: SpecialistCategory.BEAUTY,
    SpecialistType.NAIL_TECHNICIAN: SpecialistCategory.BEAUTY,
    SpecialistType.LASH_TECHNICIAN: SpecialistCategory.BEAUTY,
    SpecialistType.MAKEUP_ARTIST: SpecialistCategory.BEAUTY,
    SpecialistType.PHYSIOTHERAPIST: SpecialistCategory.WELLNESS,
    SpecialistType.NUTRITIONIST: SpecialistCategory.WELLNESS,
    SpecialistType.AROMATHERAPIST: SpecialistCategory.WELLNESS,
    SpecialistType.FITNESS_COACH: SpecialistCategory.WELLNESS,
    SpecialistType.YOGA_INSTRUCTOR: SpecialistCategory.WELLNESS,
    SpecialistType.PSYCHOLOGIST: SpecialistCategory.WELLNESS,
    SpecialistType.PSYCHOTHERAPIST: SpecialistCategory.WELLNESS,
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
    return SPECIALIST_TYPE_TO_CATEGORY.get(specialist_type, SpecialistCategory.BEAUTY)
REQUIRED_FIELDS_BY_CATEGORY = {
    SpecialistCategory.BEAUTY: [],  
    SpecialistCategory.WELLNESS: [],  
    SpecialistCategory.HEALTHCARE: [
        "allergies",  
        "chronic_diseases",  
    ],
}
