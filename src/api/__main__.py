from . import create_app

print("\n🏀 NBA Picks Bot — API REST")
print("   Backend:  http://localhost:5000")
print("   Frontend: http://localhost:5173")
print("   Detener:  Ctrl+C\n")

app = create_app()
app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
