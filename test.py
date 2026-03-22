import os
from supabase import create_client, Client

# Grab the secret variables from Render
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

# Create the database connection
supabase: Client = create_client(url, key)

# Now you can use the 'supabase' variable to talk to your database!

# This is a test program to check if Render will operate or not. 
class BankAccount:
    # 1. The Setup Function (Constructor)
    def __init__(self, owner_name, starting_balance=0.0):
        self.owner = owner_name
        self.balance = starting_balance

    # 2. A Function to Add Money
    def deposit(self, amount):
        if amount > 0:
            self.balance += amount
            print(f"Deposited ${amount}. New balance: ${self.balance}")
        else:
            print("Deposit amount must be positive.")

    # 3. A Function to Remove Money
    def withdraw(self, amount):
        if amount > 0 and amount <= self.balance:
            self.balance -= amount
            print(f"Withdrew ${amount}. New balance: ${self.balance}")
        else:
            print("Invalid withdrawal amount or insufficient funds.")

    # 4. A Function to View Data
    def check_balance(self):
        print(f"Account owner: {self.owner} | Current balance: ${self.balance}")


# --- Running the Program ---

# Create a specific instance (object) of the BankAccount class
my_account = BankAccount("Alice", 100.0)

# Call the functions attached to this specific account
my_account.check_balance()
my_account.deposit(50.0)
my_account.withdraw(20.0)

# Test the safety check we built into the withdraw function
my_account.withdraw(500.0)
