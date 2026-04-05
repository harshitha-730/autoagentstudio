import google.generativeai as genai
from dotenv import load_dotenv
import os
import time

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def generate_app(user_prompt, max_retries=3):
    """
    Generate an HTML app from a user prompt using Google Generative AI.
    Includes retry logic for rate limit handling.
    """
    system_instruction = """
    You are an expert web developer AI agent.
    When given a description of an app, you generate a complete, 
    working single-file HTML application with:
    - Clean HTML structure
    - CSS styling (modern, beautiful UI)
    - JavaScript functionality
    
    IMPORTANT RULES:
    - Return ONLY the raw HTML code
    - No explanations, no markdown, no code blocks
    - Everything in one single HTML file
    - Make it fully functional and good looking
    """
    
    full_prompt = f"{system_instruction}\n\nCreate this app: {user_prompt}"
    
    # Get the first available model
    try:
        models = genai.list_models()
        available_models = []
        for model in models:
            if "generateContent" in model.supported_generation_methods:
                # Remove 'models/' prefix for GenerativeModel
                model_name = model.name.replace("models/", "")
                available_models.append(model_name)
        
        if not available_models:
            return """
            <html>
            <head><title>No Models Available</title></head>
            <body style="background: #f0f0f0; font-family: Arial;">
            <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
                <h1>No Models Available</h1>
                <p>No generative models are available for your API key. Please check your Google Cloud project configuration.</p>
            </div>
            </body>
            </html>
            """
        
        # Use the first available model (usually the fastest/cheapest)
        model_name = available_models[0]
        print(f"Using available model: {model_name}")
        print(f"Other available models: {available_models[1:5]}")  # Show first 4 alternatives
        
    except Exception as e:
        print(f"Error listing models: {e}")
        # Fallback to a known model
        model_name = "gemini-2.0-flash"
        print(f"Using fallback model: {model_name}")
    
    try:
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        print(f"Error initializing model {model_name}: {e}")
        return f"""
        <html>
        <head><title>Model Initialization Error</title></head>
        <body style="background: #f0f0f0; font-family: Arial;">
        <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
            <h1>Model Initialization Failed</h1>
            <p>Could not initialize the generative model: {model_name}</p>
            <p style="color: #666; font-size: 0.9em;">{str(e)}</p>
        </div>
        </body>
        </html>
        """
    
    for attempt in range(max_retries):
        try:
            print(f"Generating content with {model_name} (attempt {attempt + 1}/{max_retries})...")
            response = model.generate_content(full_prompt)
            print("Content generated successfully!")
            return response.text
        except Exception as e:
            error_message = str(e)
            print(f"Error on attempt {attempt + 1}: {error_message}")
            
            # Check if it's a rate limit error
            if "429" in error_message or "RESOURCE_EXHAUSTED" in error_message:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"Rate limited. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                else:
                    return f"""
                    <html>
                    <head><title>API Quota Exceeded</title></head>
                    <body style="background: #f0f0f0; font-family: Arial;">
                    <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
                        <h1>API Quota Exceeded</h1>
                        <p>The Google Gemini API quota has been exceeded. Please:</p>
                        <ul>
                            <li>Wait a few moments and try again</li>
                            <li>Upgrade your Google Gemini API plan for higher limits</li>
                            <li>Check your billing details at <a href="https://ai.google.dev/gemini-api/docs/rate-limits" target="_blank">Google AI Documentation</a></li>
                        </ul>
                        <p style="color: #666; font-size: 0.9em;">Error: {error_message[:300]}</p>
                    </div>
                    </body>
                    </html>
                    """
            
            # For other errors on last attempt, return error message
            if attempt == max_retries - 1:
                return f"""
                <html>
                <head><title>Generation Error</title></head>
                <body style="background: #f0f0f0; font-family: Arial;">
                <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
                    <h1>Error Generating App</h1>
                    <p>An error occurred while generating your app after {max_retries} attempts:</p>
                    <pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 0.85em; max-height: 200px; overflow-y: auto;">{error_message}</pre>
                </div>
                </body>
                </html>
                """
    
    # Fallback error message
    return """
    <html>
    <head><title>Generation Failed</title></head>
    <body style="background: #f0f0f0; font-family: Arial;">
    <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
        <h1>Failed to Generate App</h1>
        <p>Unable to generate app after multiple attempts. Please try again later.</p>
    </div>
    </body>
    </html>
    """