import os

from dotenv_azd import load_azd_env

from documentchat.web import create_app

if not os.getenv("WEBSITE_HOSTNAME"):
    load_azd_env(override=False)

app = create_app()
