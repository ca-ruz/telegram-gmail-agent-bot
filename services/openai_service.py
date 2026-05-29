import os
import logging
from openai import OpenAI

# Initialize logger for this module
logger = logging.getLogger(__name__)

class OpenAIService:
    """
    Handles interactions with the OpenAI API for text and image generation.
    """
    
    def __init__(self, api_key):
        """
        Initializes the OpenAI client.
        
        Args:
            api_key (str): The OpenAI API key.
        """
        self.client = OpenAI(api_key=api_key)
        self.model = "gpt-4o"  # High-reasoning model for copywriting

    async def generate_event_promo(self, event_details, system_prompt):
        """
        Generates a Telegram post and a DALL-E prompt based on event details.
        
        Args:
            event_details (str): JSON-stringified event information.
            system_prompt (str): The instructions defining the bot's persona.
            
        Returns:
            str or None: The AI's JSON response as a string, 
                         or "QUOTA_EXCEEDED" if limits are hit.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Event Details: {event_details}"}
                ],
                response_format={ "type": "json_object" } # Ensure structured output
            )
            return response.choices[0].message.content
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("OpenAI Quota Exceeded during text generation.")
                return "QUOTA_EXCEEDED"
            logger.error(f"OpenAI Text Generation Error: {e}")
            return None

    async def generate_image(self, image_prompt):
        """
        Generates a flyer image using DALL-E 3.
        
        Args:
            image_prompt (str): The visual description for the flyer.
            
        Returns:
            str or None: The URL of the generated image, 
                         or "QUOTA_EXCEEDED" if limits are hit.
        """
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
            if "insufficient_quota" in str(e).lower():
                logger.warning("OpenAI Quota Exceeded during image generation.")
                return "QUOTA_EXCEEDED"
            logger.error(f"OpenAI Image Generation Error: {e}")
            return None
