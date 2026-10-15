from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid

app = FastAPI(title="Social Media Studio", version="1.0.0")

# In-memory storage mocks
POSTS_DB = {}
VARIANTS_DB = {}
PUBLISH_HISTORY = []
IDEMPOTENCY_KEYS = set()

# 1. Pydantic Models & Validation
class PostIngest(BaseModel):
    content: str = Field(..., min_length=10, description="Pardown text or blog post URL")

class VariantCreate(BaseModel):
    post_id: str
    platform: str = Field(..., description="Target platform: telegram, x_mock, linkedin_mock")
    text: str

class VariantUpdateStatus(BaseModel):
    status: str = Field(..., description="draft, approved, or rejected")

# Constraint profiles per platform[cite: 34, 35]
CONSTRAINT_PROFILES = {
    "telegram": {"max_length": 4000, "max_hashtags": 5},
    "x_mock": {"max_length": 280, "max_hashtags": 3},
    "linkedin_mock": {"max_length": 3000, "max_hashtags": 10}
}

def validate_against_constraints(platform: str, text: str) -> tuple[bool, str]:
    """Enforces platform-specific length and hashtag rules[cite: 34, 35]."""
    profile = CONSTRAINT_PROFILES.get(platform)
    if not profile:
        return False, f"Unknown platform: {platform}"
    
    if len(text) > profile["max_length"]:
        return False, f"Exceeds maximum length of {profile['max_length']} characters for {platform}."
    
    hashtag_count = text.count("#")
    if hashtag_count > profile["max_hashtags"]:
        return False, f"Exceeds maximum hashtag count of {profile['max_hashtags']} for {platform}."
    
    return True, "Valid"

# 2. SocialPublisher Adapter Interface & Implementations[cite: 35]
class SocialPublisher:
    async def publish(self, text: str) -> dict:
        raise NotImplementedError

class TelegramPublisher(SocialPublisher):
    async def publish(self, text: str) -> dict:
        # Real adapter for Telegram bot API[cite: 35, 38]
        # e.g., requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={...})
        return {"status": "success", "target": "telegram", "message_id": str(uuid.uuid4())}

class MockXPublisher(SocialPublisher):
    async def publish(self, text: str) -> dict:
        # Mock adapter recording post to local store[cite: 35, 38]
        return {"status": "success", "target": "x_mock", "preview": text}

class MockLinkedInPublisher(SocialPublisher):
    async def publish(self, text: str) -> dict:
        # Mock adapter recording post to local store[cite: 35, 38]
        return {"status": "success", "target": "linkedin_mock", "preview": text}

def get_publisher(platform: str) -> SocialPublisher:
    if platform == "telegram":
        return TelegramPublisher()
    elif platform == "x_mock":
        return MockXPublisher()
    elif platform == "linkedin_mock":
        return MockLinkedInPublisher()
    raise HTTPException(status_code=400, detail="Invalid publisher target")

# 3. Endpoints
@app.post("/posts", status_code=status.HTTP_201_CREATED)
async def ingest_post(payload: PostIngest):
    """Ingests a blog post as the single source of truth[cite: 35, 36]."""
    post_id = str(uuid.uuid4())
    POSTS_DB[post_id] = {"id": post_id, "content": payload.content}
    return {"status": "success", "post_id": post_id}

@app.post("/variants", status_code=status.HTTP_201_CREATED)
async def create_variant(payload: VariantCreate):
    """Generates a platform variant enforcing constraint profiles[cite: 34, 35]."""
    if payload.post_id not in POSTS_DB:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Enforce constraint profile rules before saving[cite: 35]
    is_valid, err_msg = validate_against_constraints(payload.platform, payload.text)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Constraint violation: {err_msg}")
    
    variant_id = str(uuid.uuid4())
    VARIANTS_DB[variant_id] = {
        "id": variant_id,
        "post_id": payload.post_id,
        "platform": payload.platform,
        "text": payload.text,
        "status": "draft"  # Initial state[cite: 35]
    }
    return {"status": "success", "variant_id": variant_id, "data": VARIANTS_DB[variant_id]}

@app.patch("/variants/{variant_id}/status")
async def update_variant_status(variant_id: str, payload: VariantUpdateStatus):
    """Review workflow: update variant status (draft, approved, rejected)[cite: 35]."""
    if variant_id not in VARIANTS_DB:
        raise HTTPException(status_code=404, detail="Variant not found")
    
    if payload.status not in ["draft", "approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status value")
    
    VARIANTS_DB[variant_id]["status"] = payload.status
    return {"status": "success", "variant_id": variant_id, "current_status": payload.status}

@app.post("/variants/{variant_id}/publish")
async def publish_variant(variant_id: str, idempotency_key: str):
    """
    Idempotent scheduling & publishing endpoint.
    Refuses unapproved variants with 4xx status codes[cite: 34, 35].
    """
    if variant_id not in VARIANTS_DB:
        raise HTTPException(status_code=404, detail="Variant not found")
    
    variant = VARIANTS_DB[variant_id]
    
    # Gate: Only approved variants can be scheduled/published[cite: 35]
    if variant["status"] != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot publish variant with status '{variant['status']}'. Must be 'approved'."
        )
    
    # Idempotency check: prevent duplicate posts on retry[cite: 33, 34, 35]
    if idempotency_key in IDEMPOTENCY_KEYS:
        return {
            "status": "success",
            "message": "Duplicate publish request detected; handled idempotently.",
            "idempotency_key": idempotency_key
        }
    
    # Execute publishing via adapter layer[cite: 35]
    publisher = get_publisher(variant["platform"])
    publish_result = await publisher.publish(variant["text"])
    
    # Record in publish history[cite: 35]
    history_entry = {
        "variant_id": variant_id,
        "platform": variant["platform"],
        "result": publish_result,
        "idempotency_key": idempotency_key
    }
    PUBLISH_HISTORY.append(history_entry)
    IDEMPOTENCY_KEYS.add(idempotency_key)
    
    # Update status to published[cite: 35]
    variant["status"] = "published"
    
    return {
        "status": "success",
        "message": "Variant published successfully",
        "publish_details": publish_result
    }

@app.get("/history")
async def get_publish_history():
    """Returns publish history records[cite: 35]."""
    return {"history": PUBLISH_HISTORY}