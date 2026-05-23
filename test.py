import os
from supabase import create_client, Client
from flask import Flask

# 1. Create the Web Server
app = Flask(__name__)

# 2. Test the Supabase Connection (This prints to your Render logs)
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("❌ Uh oh! The Supabase keys are missing.", flush=True)
else:
    print("✅ Keys found!", flush=True)
    try:
        supabase: Client = create_client(url, key)
        print("🎉 Supabase client successfully created!", flush=True)
    except Exception as e:
        print(f"❌ Something went wrong: {e}", flush=True)

# 3. Create a simple webpage that asks Supabase for data
@app.route('/')
def home():
    try:
        # Reach out to Supabase and grab everything in our test table
        response = supabase.table('test_connection').select('*').execute()
        
        # Extract the data from the response
        data = response.data
        
        # Return the data to the webpage!
        return f"<h1>Success!</h1> <p>Render asked Supabase for data, and Supabase said: {data}</p>"
    
    except Exception as e:
        return f"<h1>Uh oh!</h1> <p>The connection failed: {e}</p>"

# 4. Tell the server to stay awake and listen on the right port
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
