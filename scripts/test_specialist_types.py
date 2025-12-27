"""Test script for specialist types functionality."""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models.user import User
from app.db.models.specialist_profile import SpecialistProfile
from app.db.models.specialist_type import (
    SpecialistType,
    SpecialistCategory,
    get_category_for_type,
    SPECIALIST_TYPE_TO_CATEGORY,
)
from app.api.crud import crud_user, crud_specialist_profile
from app.schemas.user import UserCreate
from app.schemas.specialist_profile import SpecialistProfileCreate


def test_specialist_types():
    """Test specialist types enum and category mapping."""
    print("Testing specialist types...")
    
    # Test enum values
    assert SpecialistType.MASSAGE == "massage"
    assert SpecialistType.DENTIST == "dentist"
    assert SpecialistCategory.BEAUTY == "beauty"
    assert SpecialistCategory.HEALTHCARE == "healthcare"
    
    # Test category mapping
    assert get_category_for_type(SpecialistType.MASSAGE) == SpecialistCategory.BEAUTY
    assert get_category_for_type(SpecialistType.DENTIST) == SpecialistCategory.HEALTHCARE
    assert get_category_for_type(SpecialistType.PHYSIOTHERAPIST) == SpecialistCategory.WELLNESS
    
    print("✅ Specialist types enum and mapping working correctly")


def test_create_user_with_type():
    """Test creating a user with specialist_type."""
    db: Session = SessionLocal()
    try:
        print("\nTesting user creation with specialist_type...")
        
        user_data = UserCreate(
            full_name="Test Specialist",
            email="test_specialist@example.com",
            password="testpassword123",
            role="specialist",
            specialist_type="massage",
        )
        
        user = crud_user.create_user(db, user_data)
        assert user.role == "specialist"
        assert user.specialist_type == "massage"
        print(f"✅ User created with specialist_type: {user.specialist_type}")
        
        # Cleanup
        db.delete(user)
        db.commit()
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


def test_create_profile():
    """Test creating a specialist profile."""
    db: Session = SessionLocal()
    try:
        print("\nTesting specialist profile creation...")
        
        # Create user first
        user_data = UserCreate(
            full_name="Test Profile Specialist",
            email="test_profile@example.com",
            password="testpassword123",
            role="specialist",
            specialist_type="cosmetologist",
        )
        user = crud_user.create_user(db, user_data)
        
        # Create profile
        profile_data = SpecialistProfileCreate(
            user_id=user.id,
            specialist_type="cosmetologist",
            specialization="Facial treatments",
            experience_years=5,
            business_type="individual",
        )
        
        profile = crud_specialist_profile.create_specialist_profile(db, profile_data)
        assert profile.user_id == user.id
        assert profile.specialist_type == "cosmetologist"
        assert profile.specialist_category == "beauty"  # Should be auto-determined
        assert profile.experience_years == 5
        print(f"✅ Profile created with category: {profile.specialist_category}")
        
        # Cleanup
        db.delete(profile)
        db.delete(user)
        db.commit()
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


def test_list_types():
    """Test listing specialist types."""
    print("\nTesting list of specialist types...")
    
    beauty_types = [
        t.value for t in SpecialistType
        if SPECIALIST_TYPE_TO_CATEGORY.get(t) == SpecialistCategory.BEAUTY
    ]
    healthcare_types = [
        t.value for t in SpecialistType
        if SPECIALIST_TYPE_TO_CATEGORY.get(t) == SpecialistCategory.HEALTHCARE
    ]
    
    assert "massage" in beauty_types
    assert "dentist" in healthcare_types
    assert len(beauty_types) > 0
    assert len(healthcare_types) > 0
    
    print(f"✅ Found {len(beauty_types)} beauty types and {len(healthcare_types)} healthcare types")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Specialist Types Implementation")
    print("=" * 60)
    
    try:
        test_specialist_types()
        test_list_types()
        test_create_user_with_type()
        test_create_profile()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ Test failed: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()







