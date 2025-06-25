from app import app
from db import setupDB
from paypal import setupPaypal

if __name__ == "__main__":
    setupDB()
    setupPaypal()

    app.run()