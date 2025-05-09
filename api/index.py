import os
import json
import pickle
from datetime import datetime, timedelta
from typing import List, Any, Dict, Union
import dateutil.parser
from flask import Flask, request, render_template_string, redirect, url_for, flash, session

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import google.generativeai as genai
from pydantic import BaseModel

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# --- Configuration Constants ---
SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/tasks.readonly',
    'https://www.googleapis.com/auth/calendar.events'
]
TOKEN_PATH = 'token.json'
TOKEN_PICKLE = 'token.pickle'
CREDENTIALS_PATH = 'credentials.json'
PROMPT_FILE = 'prompt.txt'
OUTPUT_EVENTS = 'events.json'
GEMINI_MODEL = 'gemini-2.5-pro-exp-03-25'

# FIXED: Make sure this EXACTLY matches one of the authorized redirect URIs
# in your Google Cloud Console
REDIRECT_URI = 'https://flask-hello-world-zeta-gules.vercel.app/auth'

# --- Pydantic Model for Structured Output ---
class CalendarEvent(BaseModel):
    summary: str
    start_datetime: Union[datetime, str]
    end_datetime: Union[datetime, str]
    description: str | None = None
    location: str | None = None
    color_id: str | None = None
    attendees: list[str] | None = None
    timezone: str | None = None
    
    def model_dump(self) -> Dict[str, Any]:
        """Convert to a dictionary, ensuring datetime fields are strings"""
        data = super().model_dump()
        
        # Convert datetime objects to strings if necessary
        for field in ["start_datetime", "end_datetime"]:
            if isinstance(data[field], datetime):
                data[field] = data[field].isoformat()
        
        return data

# --- HTML Templates ---
HOME_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Calendar Automation</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; line-height: 1.6; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #333; }
        form { margin: 20px 0; }
        textarea { width: 100%; height: 150px; padding: 12px; box-sizing: border-box; margin: 6px 0; }
        input[type="submit"], input[type="text"] { padding: 10px 15px; box-sizing: border-box; }
        input[type="submit"] { background-color: #4CAF50; color: white; border: none; cursor: pointer; }
        input[type="submit"]:hover { background-color: #45a049; }
        input[type="text"] { width: 100%; margin: 6px 0; }
        .flash { padding: 10px; margin: 10px 0; border-radius: 5px; }
        .flash.success { background-color: #dff0d8; color: #3c763d; }
        .flash.error { background-color: #f2dede; color: #a94442; }
        .steps { margin-top: 30px; }
        .step { margin-bottom: 15px; padding: 10px; background-color: #f9f9f9; border-radius: 4px; }
        .step.completed { background-color: #dff0d8; }
        pre { background-color: #f5f5f5; padding: 10px; overflow-x: auto; }
        .auth-notice { background-color: #fcf8e3; padding: 15px; margin: 20px 0; border-radius: 4px; }
        a.button { display: inline-block; padding: 10px 15px; background-color: #337ab7; color: white; 
                  text-decoration: none; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Calendar Event Generator</h1>
        
        {% if messages %}
            {% for message in messages %}
                <div class="flash {{ message.type }}">{{ message.text }}</div>
            {% endfor %}
        {% endif %}
        
        {% if needs_auth %}
            <div class="auth-notice">
                <h3>Authentication Required</h3>
                <p>You need to authenticate with Google Calendar to use this application.</p>
                <a href="/auth" class="button">Start Authentication</a>
            </div>
        {% else %}
            <form method="POST" action="/generate">
                <h3>What type of day would you like?</h3>
                <p>Describe how you would like your day to be organized and what events to create:</p>
                <textarea name="custom_prompt" placeholder="Example: I need a productive day with focused work sessions in the morning and meetings in the afternoon. Schedule lunch at noon.">{{ custom_prompt }}</textarea>
                <input type="submit" value="Generate & Create Calendar Events">
            </form>
            
            <div class="steps">
                <h3>Process Steps:</h3>
                
                <div class="step {% if step_fetch %}completed{% endif %}">
                    <h4>1. Fetch Current Calendar & Tasks</h4>
                    {% if events %}
                        <p>Fetched {{ events|length }} calendar events and {{ tasks|length }} tasks.</p>
                    {% else %}
                        <p>Not started yet</p>
                    {% endif %}
                </div>
                
                <div class="step {% if step_generate %}completed{% endif %}">
                    <h4>2. Generate Suggested Events</h4>
                    {% if generated_events %}
                        <p>Generated {{ generated_events|length }} calendar events:</p>
                        <pre>{{ generated_events_json }}</pre>
                    {% else %}
                        <p>Not started yet</p>
                    {% endif %}
                </div>
                
                <div class="step {% if step_create %}completed{% endif %}">
                    <h4>3. Create Events in Google Calendar</h4>
                    {% if created_events %}
                        <p>Created {{ created_events|length }} events in your calendar.</p>
                        <ul>
                            {% for event in created_events %}
                                <li>{{ event.summary }} - <a href="{{ event.link }}" target="_blank">View in Calendar</a></li>
                            {% endfor %}
                        </ul>
                    {% else %}
                        <p>Not started yet</p>
                    {% endif %}
                </div>
            </div>
        {% endif %}
    </div>
</body>
</html>
'''

AUTH_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Google Authentication</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; line-height: 1.6; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #333; }
        .auth-box { background-color: #f9f9f9; padding: 20px; border-radius: 4px; margin: 20px 0; }
        .auth-steps { background-color: #fcf8e3; padding: 20px; border-radius: 4px; margin: 20px 0; }
        ol li { margin-bottom: 10px; }
        input[type="text"] { width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; }
        input[type="submit"] { background-color: #4CAF50; color: white; padding: 10px 15px; 
                              border: none; cursor: pointer; }
        input[type="submit"]:hover { background-color: #45a049; }
        .flash { padding: 10px; margin: 10px 0; border-radius: 5px; }
        .flash.error { background-color: #f2dede; color: #a94442; }
        .auth-debug { margin-top: 20px; padding: 10px; background-color: #f5f5f5; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Google Calendar Authentication</h1>
        
        {% if error %}
            <div class="flash error">{{ error }}</div>
        {% endif %}
        
        <div class="auth-box">
            <h2>Authentication Required</h2>
            <p>To access your Google Calendar, you need to authorize this application.</p>
            
            <div class="auth-steps">
                <h3>Follow these steps:</h3>
                <ol>
                    <li>Click on the link below to open Google's authorization page</li>
                    <li>Sign in to your Google account and grant the requested permissions</li>
                    <li>You will be redirected back automatically, but if that doesn't work:</li>
                    <li>Copy the authorization code provided by Google</li>
                    <li>Paste the code below and click Submit</li>
                </ol>
            </div>
            
            <h3>1. Open this link:</h3>
            <a href="{{ auth_url }}" target="_blank">{{ auth_url }}</a>
            
            <h3>2. Or enter the authorization code if not automatically redirected:</h3>
            <form method="POST" action="/auth">
                <input type="text" name="code" placeholder="Paste authorization code here" required>
                <input type="submit" value="Submit">
            </form>
            
            <!-- Debug info for troubleshooting -->
            <div class="auth-debug">
                <h4>Debug Information:</h4>
                <p>Current Redirect URI: {{ redirect_uri }}</p>
            </div>
        </div>
    </div>
</body>
</html>
'''

# --- Google API Authentication Functions ---
def get_google_services():
    """Authenticate and return Calendar and Tasks services with browser flow"""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Use web flow for browser-based auth
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_PATH, 
                    SCOPES,
                    # FIXED: Use redirect_uri from constants and don't set it to oob
                    redirect_uri=REDIRECT_URI
                )
                # FIXED: Set access_type to offline to get refresh token
                auth_url, _ = flow.authorization_url(
                    prompt='consent',
                    access_type='offline'
                )
                
                # Store the auth URL in session for the auth page
                session['auth_url'] = auth_url
                session['auth_flow'] = 'active'
                
                # Redirect to auth page to get the code
                return None, None
            except Exception as e:
                app.logger.error(f"Auth flow error: {e}")
                raise ValueError(f"Authentication failed: {e}")
                
        # Save tokens once we have them
        with open(TOKEN_PATH, 'w') as token_file:
            token_file.write(creds.to_json())
            
        # Also save as pickle for compatibility with create_calendar_events
        with open(TOKEN_PICKLE, 'wb') as token_pickle:
            pickle.dump(creds, token_pickle)
            
    cal_service = build('calendar', 'v3', credentials=creds)
    tasks_service = build('tasks', 'v1', credentials=creds)
    return cal_service, tasks_service

def get_calendar_service():
    """Get just the calendar service for creating events"""
    creds = None
    if os.path.exists(TOKEN_PICKLE):
        with open(TOKEN_PICKLE, 'rb') as t:
            creds = pickle.load(t)
    if not creds or not creds.valid:
        # Use the same creds from the main authentication
        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if not creds or not creds.valid:
            raise ValueError("Please authenticate first via the homepage")
            
        # Save as pickle
        with open(TOKEN_PICKLE, 'wb') as t:
            pickle.dump(creds, t)
            
    return build('calendar', 'v3', credentials=creds)

# --- Calendar & Tasks Functions ---
def fetch_todays_events(cal_service) -> List[dict]:
    """Fetch today's calendar events"""
    now = datetime.utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
    
    resp = cal_service.events().list(
        calendarId='primary',
        timeMin=start,
        timeMax=end,
        singleEvents=True,
        orderBy='startTime',
        fields='items(id,summary,start,end,location,description)'
    ).execute()
    return resp.get('items', [])

def fetch_tasks(tasks_service) -> List[dict]:
    """Fetch all tasks with deadlines"""
    resp = tasks_service.tasks().list(
        tasklist='@default',
        fields='items(id,title,due,parent,notes)'
    ).execute()
    return resp.get('items', [])

def build_full_prompt(events: List[dict], tasks: List[dict], custom: str) -> str:
    """Build a prompt combining calendar events, tasks, and custom instructions"""
    header = "Today's calendar events:\n"
    for ev in events:
        start = ev['start'].get('dateTime', ev['start'].get('date'))
        end = ev['end'].get('dateTime', ev['end'].get('date'))
        header += f"- {ev.get('summary', '(no title)')} from {start} to {end}\n"
    
    header += "\nToday's tasks with deadlines and parents:\n"
    for t in tasks:
        header += f"- {t.get('title', '(no title)')}, due {t.get('due', 'none')}"
        if t.get('parent'):
            header += f", subtask of {t['parent']}"
        header += "\n"
    
    return header + "\n" + custom

def generate_events(prompt: str) -> List[CalendarEvent]:
    """Use Gemini to generate structured calendar events from prompt"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    
    # Initialize the Gemini API
    genai.configure(api_key=api_key)
    
    # Get the model
    model = genai.GenerativeModel(GEMINI_MODEL)
    
    # Generate content with structured output
    resp = model.generate_content(
        contents=prompt,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "start_datetime": {"type": "string", "format": "date-time"},
                        "end_datetime": {"type": "string", "format": "date-time"},
                        "description": {"type": "string", "nullable": True},
                        "location": {"type": "string", "nullable": True},
                        "color_id": {"type": "string", "nullable": True},
                        "attendees": {"type": "array", "items": {"type": "string"}, "nullable": True},
                        "timezone": {"type": "string", "nullable": True}
                    },
                    "required": ["summary", "start_datetime", "end_datetime"]
                }
            }
        }
    )
    
    # Parse the response
    result = json.loads(resp.text)
    
    # Convert JSON to CalendarEvent objects
    calendar_events = []
    for event_data in result:
        calendar_events.append(CalendarEvent(**event_data))
    
    return calendar_events

def create_calendar_event(service: Any, ev: dict) -> dict:
    """Create a single event in Google Calendar"""
    # Convert datetime strings to RFC3339 format if necessary
    try:
        # Try to parse the datetime string using dateutil (handles more formats)
        start_dt = dateutil.parser.parse(ev['start_datetime'])
        end_dt = dateutil.parser.parse(ev['end_datetime'])
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()
    except Exception as e:
        app.logger.warning(f"DateTime parsing error: {e}")
        # Fallback: replace space with 'T' if it's a string
        if isinstance(ev['start_datetime'], str):
            start_iso = ev['start_datetime'].replace(' ', 'T')
        else:
            start_iso = str(ev['start_datetime'])
            
        if isinstance(ev['end_datetime'], str):
            end_iso = ev['end_datetime'].replace(' ', 'T')
        else:
            end_iso = str(ev['end_datetime'])

    # Ensure we have the correct RFC3339 format with timezone
    if 'Z' not in start_iso and '+' not in start_iso and '-' not in start_iso[10:]:
        start_iso += 'Z'
    if 'Z' not in end_iso and '+' not in end_iso and '-' not in end_iso[10:]:
        end_iso += 'Z'

    event_body = {
        'summary': ev['summary'],
        'start': {
            'dateTime': start_iso,
            'timeZone': ev.get('timezone', 'UTC')
        },
        'end': {
            'dateTime': end_iso,
            'timeZone': ev.get('timezone', 'UTC')
        }
    }
    
    # Add optional fields if present
    for key in ('description', 'location', 'color_id'):
        if ev.get(key):
            k = 'colorId' if key == 'color_id' else key
            event_body[k] = ev[key]
    
    if ev.get('attendees'):
        event_body['attendees'] = [{'email': email} for email in ev['attendees']]

    # Create the event
    try:
        return service.events().insert(
            calendarId='primary',
            body=event_body
        ).execute()
    except HttpError as e:
        error_content = e.content.decode() if hasattr(e, 'content') else str(e)
        app.logger.error(f"Error creating event '{ev['summary']}': {error_content}")
        raise

# --- Flask Routes ---
@app.route('/')
def home():
    """Home page with form and status"""
    context = {
        'messages': session.pop('messages', []),
        'custom_prompt': session.get('custom_prompt', ''),
        'events': session.get('events', []),
        'tasks': session.get('tasks', []),
        'generated_events': session.get('generated_events', []),
        'generated_events_json': session.get('generated_events_json', ''),
        'created_events': session.get('created_events', []),
        'step_fetch': session.get('step_fetch', False),
        'step_generate': session.get('step_generate', False),
        'step_create': session.get('step_create', False),
        'needs_auth': False
    }
    
    # Check if we need authentication
    try:
        if not os.path.exists(TOKEN_PATH):
            context['needs_auth'] = True
        else:
            # Check if creds are valid
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            if not creds.valid and (not creds.expired or not creds.refresh_token):
                context['needs_auth'] = True
    except Exception:
        context['needs_auth'] = True
        
    return render_template_string(HOME_TEMPLATE, **context)

@app.route('/auth', methods=['GET', 'POST'])
def auth():
    """Handle OAuth authentication"""
    # Debug the current redirect URI being used
    app.logger.info(f"Using redirect URI: {REDIRECT_URI}")
    
    # Handle direct callback from Google OAuth
    if request.method == 'GET' and request.args.get('code'):
        code = request.args.get('code')
        try:
            # Create flow and set redirect URI
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_PATH, 
                SCOPES,
                redirect_uri=REDIRECT_URI
            )
            
            # Exchange the code for credentials
            flow.fetch_token(code=code)
            creds = flow.credentials
            
            # Save tokens
            with open(TOKEN_PATH, 'w') as token_file:
                token_file.write(creds.to_json())
                
            # Also save as pickle for compatibility
            with open(TOKEN_PICKLE, 'wb') as token_pickle:
                pickle.dump(creds, token_pickle)
                
            # Clear the auth session data
            session.pop('auth_url', None)
            session.pop('auth_flow', None)
            
            # Add success message
            session['messages'] = [{'type': 'success', 'text': 'Authentication successful!'}]
            
            # Redirect back to home
            return redirect(url_for('home'))
            
        except Exception as e:
            app.logger.error(f"Error in auth flow: {e}")
            session['messages'] = [{'type': 'error', 'text': f"Authentication failed: {str(e)}"}]
            return redirect(url_for('home'))
    
    # Handle form submission (for manual code entry as fallback)
    elif request.method == 'POST':
        # Process the code from form submission
        code = request.form.get('code')
        if not code:
            return render_template_string(AUTH_TEMPLATE, 
                                         auth_url=session.get('auth_url', ''),
                                         redirect_uri=REDIRECT_URI,
                                         error="Authorization code is required")
        
        try:
            # Create flow and set redirect URI
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_PATH, 
                SCOPES,
                redirect_uri=REDIRECT_URI
            )
            
            # Exchange the code for credentials
            flow.fetch_token(code=code)
            creds = flow.credentials
            
            # Save tokens
            with open(TOKEN_PATH, 'w') as token_file:
                token_file.write(creds.to_json())
                
            # Also save as pickle for compatibility
            with open(TOKEN_PICKLE, 'wb') as token_pickle:
                pickle.dump(creds, token_pickle)
                
            # Clear the auth session data
            session.pop('auth_url', None)
            session.pop('auth_flow', None)
            
            # Add success message
            session['messages'] = [{'type': 'success', 'text': 'Authentication successful!'}]
            
            # Redirect back to home
            return redirect(url_for('home'))
            
        except Exception as e:
            app.logger.error(f"Error in auth flow: {e}")
            return render_template_string(AUTH_TEMPLATE, 
                                         auth_url=session.get('auth_url', ''),
                                         redirect_uri=REDIRECT_URI,
                                         error=f"Authentication failed: {str(e)}")
    else:
        # GET request without code - show auth page
        # Check if we need to start the auth flow
        if not session.get('auth_url'):
            try:
                # Create a new flow and get the URL
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_PATH, 
                    SCOPES,
                    redirect_uri=REDIRECT_URI
                )
                # FIXED: Set access_type to offline to get refresh token
                auth_url, _ = flow.authorization_url(
                    prompt='consent',
                    access_type='offline'
                )
                session['auth_url'] = auth_url
            except Exception as e:
                app.logger.error(f"Error starting auth flow: {e}")
                return render_template_string(AUTH_TEMPLATE, 
                                             auth_url="",
                                             redirect_uri=REDIRECT_URI,
                                             error=f"Error starting authentication: {str(e)}")
                
        return render_template_string(AUTH_TEMPLATE, 
                                     auth_url=session.get('auth_url', ''),
                                     redirect_uri=REDIRECT_URI,
                                     error=None)

@app.route('/generate', methods=['POST'])
def generate():
    """Process the workflow: fetch → generate → create"""
    messages = []
    session['messages'] = []
    
    # Get custom prompt from form
    custom_prompt = request.form.get('custom_prompt', '')
    session['custom_prompt'] = custom_prompt
    
    try:
        # Step 1: Fetch calendar events and tasks
        cal_service, tasks_service = get_google_services()
        
        # Check if we need authentication
        if cal_service is None and session.get('auth_flow') == 'active':
            return redirect(url_for('auth'))
            
        events = fetch_todays_events(cal_service)
        tasks = fetch_tasks(tasks_service)
        
        session['events'] = events
        session['tasks'] = tasks
        session['step_fetch'] = True
        
        # Build and save prompt
        full_prompt = build_full_prompt(events, tasks, custom_prompt)
        with open(PROMPT_FILE, 'w', encoding='utf-8') as f:
            f.write(full_prompt)
        
        messages.append({
            'type': 'success', 
            'text': f'Fetched {len(events)} events and {len(tasks)} tasks'
        })
        
        # Step 2: Generate structured events using Gemini
        try:
            generated_events = generate_events(full_prompt)
            
            # Convert pydantic models to dicts for JSON serialization
            event_dicts = [ev.model_dump() for ev in generated_events]
            
            # Save to events.json
            with open(OUTPUT_EVENTS, 'w', encoding='utf-8') as f:
                json.dump(event_dicts, f, default=str, indent=2)
            
            session['generated_events'] = event_dicts
            session['generated_events_json'] = json.dumps(event_dicts, default=str, indent=2)
            session['step_generate'] = True
            
            messages.append({
                'type': 'success', 
                'text': f'Generated {len(generated_events)} calendar events'
            })
            
            # Step 3: Create events in Google Calendar
            calendar_service = get_calendar_service()
            created_events = []
            
            for ev in event_dicts:
                try:
                    created = create_calendar_event(calendar_service, ev)
                    created_events.append({
                        'summary': ev['summary'],
                        'link': created.get('htmlLink', '#')
                    })
                except Exception as create_err:
                    messages.append({
                        'type': 'error',
                        'text': f"Error creating event '{ev['summary']}': {str(create_err)}"
                    })
            
            session['created_events'] = created_events
            session['step_create'] = True
            
            messages.append({
                'type': 'success',
                'text': f'Created {len(created_events)} events in your Google Calendar'
            })
            
        except Exception as gen_err:
            messages.append({
                'type': 'error',
                'text': f'Error generating events: {str(gen_err)}'
            })
    
    except Exception as e:
        messages.append({'type': 'error', 'text': f'Error: {str(e)}'})
    
    session['messages'] = messages
    return redirect(url_for('home'))

@app.route('/about')
def about():
    return 'Calendar Automation App'

if __name__ == '__main__':
    app.run(debug=True)