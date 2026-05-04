# simple_ctf.py

# Задаём правильный пароль
correct_password = "CTF{first_level}"

# Запрашиваем пароль у пользователя
user_input = input("Enter the password: ")

# Проверяем пароль
if user_input == correct_password:
    print("Access granted! 🎉")
else:
    print("Access denied! ❌")

