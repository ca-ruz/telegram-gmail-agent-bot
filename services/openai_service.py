import os
from openai import OpenAI

class OpenAIService:
    def __init__(self, api_key):
        self.client = OpenAI(api_key=api_key)
        self.model = "gpt-4o"  # Using GPT-4o for best reasoning and copy

    async def generate_event_promo(self, event_details, system_prompt):
        """Generates both the Telegram copy and the DALL-E prompt."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Event Details: {event_details}"}
                ],
                response_format={ "type": "json_object" } # Force JSON output
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating promo text: {e}")
            return None

    async def generate_image(self, image_prompt):
        """Triggers DALL-E 3 to create the flyer."""
        try:
            response = self.client.images.generate(
                model="dall-e-3",
                prompt=image_prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            return response.data[0].url
        except Exception as e:
            # Check for specific quota/limit errors
            if "insufficient_quota" in str(e).lower():
                print("OpenAI Error: Insufficient Quota.")
                return "QUOTA_EXCEEDED"
            print(f"Error generating image: {e}")
            return None
