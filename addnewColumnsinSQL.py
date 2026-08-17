import sqlite3

conn = sqlite3.connect("appointments_new.db")
c = conn.cursor()

# Add date column if missing
#c.execute("ALTER TABLE professionals ADD COLUMN pin INTEGER")
c.execute("UPDATE professionals SET business_type='Doctor', city='Bangalore', establishment='Aadi Global Hospitals' WHERE id=1")
c.execute("UPDATE professionals SET pin='560097', city='Bangalore', establishment='Aadi Global Hospitals' WHERE id=1")

conn.commit()
conn.close()
