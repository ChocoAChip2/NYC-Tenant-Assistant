import os
from supabase import create_client, Client

# Grab the secret variables from Render
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

# Create the database connection
supabase: Client = create_client(url, key)

# Now you can use the 'supabase' variable to talk to your database!

# Let's check if the code actually found the secret keys!
if not url or not key:
    print("❌ Uh oh! The Supabase keys are missing.")
else:
    print("✅ Keys found! Attempting to set up Supabase client...")
    try:
        supabase: Client = create_client(url, key)
        print("🎉 Supabase client successfully created!")
    except Exception as e:
        print(f"❌ Something went wrong: {e}")
