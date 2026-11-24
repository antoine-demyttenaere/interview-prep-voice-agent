"""Sign in with Google: checks the token Google gives the web page, and returns who it is.

Used by POST /auth/google (app/auth/routes.py). The web page (web/app.js) shows Google's
"Continue with Google" button (Google Identity Services, a script from accounts.google.com).
When you pick your Google account, Google hands the page an "ID token", and the page sends it here.

- An ID token is a JWT: a small JSON document (your Google id "sub", your email, whether Google
  checked that email, which app it was made for, when it expires) signed by Google. Checking the
  signature with Google's public keys proves Google wrote it, so nobody can forge one.
- The token must be made for OUR app: its "aud" (audience) must be GOOGLE_CLIENT_ID. A token
  some other website got for you would be refused.
- google-auth (the official library) does the checks and fetches and caches Google's keys.

To turn it on: create an "OAuth client ID" of type "Web application" in Google Cloud Console
(APIs & Services > Credentials), add http://localhost:8000 (and later the AWS address) under
"Authorized JavaScript origins", then put its id in .env as GOOGLE_CLIENT_ID and recreate the
api container. Without it, the page doesn't show the button and /auth/google answers 404.
The tests replace verify_token() with a fake (tests/test_google_login.py).
"""

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

_transport = google_requests.Request()  # reused, so Google's public keys stay cached


def verify_token(credential: str, client_id: str) -> dict:
    """The token's claims ({"sub", "email", "email_verified", ...}) if it is genuine, made for
    this app and not expired. Raises ValueError otherwise."""
    claims = id_token.verify_oauth2_token(credential, _transport, client_id)
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("not issued by Google")
    return claims
