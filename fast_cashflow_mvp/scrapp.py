import requests

# Pega aquí tu TOKEN real
TOKEN = "8453773053:AAH9pIz6tR3rWkp8a-V_Swx9NquVbIlYD_c"

url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
response = requests.get(url).json()

try:
    chat_id = response['result'][0]['message']['chat']['id']
    print(f"✅ Tu CHAT_ID es: {chat_id}")
except (IndexError, KeyError):
    print("❌ No se encontraron mensajes. ¿Ya le enviaste 'Hola' a tu bot en Telegram?")