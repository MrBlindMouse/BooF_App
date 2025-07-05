from app import app
from db import setupDB
from paypal import setupPaypal

if __name__ == "__main__":
    setupDB()
    setupPaypal('https://www.boof-bots.com/hook')

    print("Prod server running!!!")

    app.run(port="5005")
