from flask import Flask, request

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        # grab the form field named "text_input"
        user_input = request.form.get('text_input', '')
        # simply return a bit of HTML showing what you typed
        return f'''
            <h1>You entered:</h1>
            <p>{user_input}</p>
            <a href="/">Try again</a>
        '''
    # if GET, show a plain HTML form
    return '''
        <form method="POST">
            <input type="text" name="text_input" placeholder="Type here">
            <input type="submit" value="Submit">
        </form>
    '''

@app.route('/about')
def about():
    return 'About'

if __name__ == '__main__':
    app.run(debug=True)
