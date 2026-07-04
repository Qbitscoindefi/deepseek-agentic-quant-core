from google_auth_oauthlib.flow import InstalledAppFlow

# Forzamos la autenticación web usando el flujo oficial de desarrollo
flow = InstalledAppFlow.from_client_config(
    {"web": {"client_id": "823456789-test.apps.googleusercontent.com", "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}},
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
flow.run_local_server(port=8080)
print("✅ Autenticación completada con éxito.")
