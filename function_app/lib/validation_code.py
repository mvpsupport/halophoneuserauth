"""
SMS validation code management.
Generates, stores, and validates one-time use codes.
"""
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# In-memory cache for validation codes
# In production, consider using Redis or Azure Cache for Redis
CODE_CACHE: Dict[str, Dict[str, Any]] = {}


def generate_numeric_code(length: int = 6) -> str:
    """
    Generate a random numeric code.
    
    Args:
        length: Length of the code (default 6 digits)
        
    Returns:
        Numeric string code
    """
    return "".join(random.choices(string.digits, k=length))


def generate_alphanumeric_code(length: int = 8) -> str:
    """
    Generate a random alphanumeric code.
    
    Args:
        length: Length of the code (default 8 characters)
        
    Returns:
        Alphanumeric string code
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def store_validation_code(
    phone_number: str,
    code: str,
    ttl_minutes: int = 10,
) -> Dict[str, Any]:
    """
    Store a validation code with expiration time.
    
    Args:
        phone_number: Phone number associated with code
        code: The validation code to store
        ttl_minutes: Time to live in minutes (default 10)
        
    Returns:
        Dictionary with code, expires_at, and phone_number
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    
    entry = {
        "code": code,
        "expires_at": expires_at,
        "phone_number": phone_number,
        "created_at": datetime.now(timezone.utc),
    }
    
    CODE_CACHE[phone_number] = entry
    logger.info("Stored validation code for phone: %s (expires in %d minutes)", phone_number, ttl_minutes)
    
    return entry


def verify_code(phone_number: str, code: str) -> bool:
    """
    Verify a code against the stored entry.
    
    Args:
        phone_number: Phone number to verify against
        code: Code to verify
        
    Returns:
        True if code is valid and not expired, False otherwise
    """
    entry = CODE_CACHE.get(phone_number)
    
    if not entry:
        logger.warning("No code stored for phone: %s", phone_number)
        return False
    
    now = datetime.now(timezone.utc)
    if now > entry.get("expires_at"):
        logger.info("Code expired for phone: %s", phone_number)
        del CODE_CACHE[phone_number]
        return False
    
    if entry.get("code") != code:
        logger.warning("Invalid code provided for phone: %s", phone_number)
        return False
    
    # Code verified successfully, remove it
    del CODE_CACHE[phone_number]
    logger.info("Code verified successfully for phone: %s", phone_number)
    return True


def get_code_info(phone_number: str) -> Optional[Dict[str, Any]]:
    """
    Get information about a stored code (for debugging/admin purposes).
    
    Args:
        phone_number: Phone number to get code info for
        
    Returns:
        Code info dict or None if not found
    """
    entry = CODE_CACHE.get(phone_number)
    if not entry:
        return None
    
    now = datetime.now(timezone.utc)
    expires_at = entry.get("expires_at")
    
    return {
        "phone_number": phone_number,
        "created_at": entry.get("created_at"),
        "expires_at": expires_at,
        "is_expired": now > expires_at,
        "time_remaining_seconds": int((expires_at - now).total_seconds()),
    }


def cleanup_expired_codes() -> int:
    """
    Remove all expired codes from the cache.
    Call this periodically to prevent memory bloat.
    
    Returns:
        Number of codes removed
    """
    now = datetime.now(timezone.utc)
    expired_phones = [
        phone for phone, entry in CODE_CACHE.items()
        if now > entry.get("expires_at")
    ]
    
    for phone in expired_phones:
        del CODE_CACHE[phone]
    
    if expired_phones:
        logger.info("Cleaned up %d expired codes", len(expired_phones))
    
    return len(expired_phones)
