"""
Halo PSA API integration for retrieving user information.
Provides methods to fetch agent and company details from Halo PSA.
"""
import logging
import os
from typing import Any, Dict, Optional

import requests


class HaloPSAClient:
    """Client for interacting with Halo PSA API."""

    def __init__(self, client_id: str, client_secret: str, tenant_id: str):
        """
        Initialize Halo PSA client with credentials.
        
        Args:
            client_id: Halo PSA OAuth2 client ID
            client_secret: Halo PSA OAuth2 client secret
            tenant_id: Halo PSA tenant identifier
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.base_url = f"https://{tenant_id}.halospirit.com/api"
        self._access_token: Optional[str] = None
        self.logger = logging.getLogger(__name__)

    def _get_access_token(self) -> str:
        """
        Acquire an OAuth2 access token from Halo PSA.
        
        Returns:
            Access token string
            
        Raises:
            RuntimeError: If token acquisition fails
        """
        if self._access_token:
            return self._access_token

        url = f"{self.base_url}/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "all",
        }

        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            self._access_token = result.get("access_token")
            if not self._access_token:
                raise RuntimeError("No access token in response")
            return self._access_token
        except requests.RequestException as e:
            self.logger.error("Failed to acquire Halo PSA token: %s", e)
            raise RuntimeError(f"Unable to authenticate with Halo PSA: {e}")

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers with authorization token."""
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_agent_by_phone(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve agent information by phone number.
        
        Args:
            phone_number: Phone number to search for
            
        Returns:
            Agent details dict with id, name, email, phone fields or None if not found
        """
        try:
            url = f"{self.base_url}/agents"
            params = {"search": phone_number, "pagesize": 1}
            response = requests.get(
                url,
                params=params,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()
            
            agents = response.json()
            if not agents or len(agents) == 0:
                self.logger.info("No agent found for phone number: %s", phone_number)
                return None
                
            agent = agents[0]
            return {
                "id": agent.get("id"),
                "name": agent.get("name", ""),
                "email": agent.get("email_address", ""),
                "phone": agent.get("phone_number", ""),
            }
        except requests.RequestException as e:
            self.logger.error("Failed to get agent by phone: %s", e)
            return None

    def get_agent_by_id(self, agent_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve agent information by agent ID.
        
        Args:
            agent_id: Halo PSA agent ID
            
        Returns:
            Agent details dict or None if not found
        """
        try:
            url = f"{self.base_url}/agents/{agent_id}"
            response = requests.get(
                url,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()
            
            agent = response.json()
            return {
                "id": agent.get("id"),
                "name": agent.get("name", ""),
                "email": agent.get("email_address", ""),
                "phone": agent.get("phone_number", ""),
            }
        except requests.RequestException as e:
            self.logger.error("Failed to get agent by ID: %s", e)
            return None

    def get_company_by_id(self, company_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve company information by company ID.
        
        Args:
            company_id: Halo PSA company ID
            
        Returns:
            Company details dict or None if not found
        """
        try:
            url = f"{self.base_url}/companies/{company_id}"
            response = requests.get(
                url,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()
            
            company = response.json()
            return {
                "id": company.get("id"),
                "name": company.get("name", ""),
            }
        except requests.RequestException as e:
            self.logger.error("Failed to get company by ID: %s", e)
            return None
