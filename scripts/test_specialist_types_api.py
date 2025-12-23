"""Test script for specialist types API endpoints."""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_get_types():
    """Test GET /specialist-profiles/types endpoint."""
    print("\n" + "="*60)
    print("Testing GET /specialist-profiles/types")
    print("="*60)
    
    try:
        response = requests.get(f"{BASE_URL}/specialist-profiles/types")
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ Success!")
            print(f"Categories: {list(data.get('categories', {}).keys())}")
            for category, info in data.get('categories', {}).items():
                print(f"  - {category}: {info.get('name')} ({len(info.get('types', []))} types)")
            return True
        else:
            print(f"❌ Error: {response.text}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to server. Make sure backend is running:")
        print("   uvicorn app.main:app --reload")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_create_user_with_type():
    """Test creating a user with specialist_type."""
    print("\n" + "="*60)
    print("Testing POST /users/ with specialist_type")
    print("="*60)
    
    test_user = {
        "full_name": "Test Specialist",
        "email": "test_specialist_types@example.com",
        "password": "TestP@ss123!",
        "role": "specialist",
        "specialist_type": "massage"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/users/",
            json=test_user,
            headers={"Content-Type": "application/json"}
        )
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ User created successfully!")
            print(f"   ID: {data.get('id')}")
            print(f"   Name: {data.get('full_name')}")
            print(f"   Role: {data.get('role')}")
            print(f"   Specialist Type: {data.get('specialist_type')}")
            return data.get('id')
        else:
            print(f"❌ Error: {response.text}")
            # Check if user already exists
            if "already exists" in response.text.lower() or response.status_code == 400:
                print("   (User might already exist, that's OK)")
            return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def test_create_profile(user_id, token):
    """Test creating a specialist profile."""
    print("\n" + "="*60)
    print("Testing POST /specialist-profiles/")
    print("="*60)
    
    profile_data = {
        "user_id": user_id,
        "specialist_type": "massage",
        "specialization": "Deep tissue massage",
        "experience_years": 5,
        "business_type": "individual"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/specialist-profiles/",
            json=profile_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
        )
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ Profile created successfully!")
            print(f"   Profile ID: {data.get('id')}")
            print(f"   Specialist Type: {data.get('specialist_type')}")
            print(f"   Category: {data.get('specialist_category')}")
            print(f"   Experience: {data.get('experience_years')} years")
            return True
        else:
            print(f"❌ Error: {response.text}")
            if "already exists" in response.text.lower():
                print("   (Profile already exists, that's OK)")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_get_profile(token):
    """Test GET /specialist-profiles/me endpoint."""
    print("\n" + "="*60)
    print("Testing GET /specialist-profiles/me")
    print("="*60)
    
    try:
        response = requests.get(
            f"{BASE_URL}/specialist-profiles/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ Profile retrieved successfully!")
            print(f"   Specialist Type: {data.get('specialist_type')}")
            print(f"   Category: {data.get('specialist_category')}")
            return True
        else:
            print(f"❌ Error: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    """Run all tests."""
    print("="*60)
    print("SPECIALIST TYPES API TESTING")
    print("="*60)
    print("\nMake sure backend is running: uvicorn app.main:app --reload")
    
    # Test 1: Get types (no auth required)
    if not test_get_types():
        print("\n⚠️  Cannot continue without types endpoint")
        return
    
    # Test 2: Create user with specialist_type (no auth required)
    user_id = test_create_user_with_type()
    
    if not user_id:
        print("\n⚠️  Cannot continue without creating user")
        print("   Try logging in with existing user and get token")
        return
    
    # Login to get token
    print("\n" + "="*60)
    print("Logging in to get token...")
    print("="*60)
    
    login_data = {
        "email": "test_specialist_types@example.com",
        "password": "TestP@ss123!"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/users/login", json=login_data)
        if response.status_code == 200:
            token = response.json().get('access_token')
            print(f"✅ Login successful, token received")
            
            # Test 3: Create profile
            test_create_profile(user_id, token)
            
            # Test 4: Get profile
            test_get_profile(token)
        else:
            print(f"❌ Login failed: {response.text}")
    except Exception as e:
        print(f"❌ Login error: {e}")
    
    print("\n" + "="*60)
    print("Testing complete!")
    print("="*60)

if __name__ == "__main__":
    main()




