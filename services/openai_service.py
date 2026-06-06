import logging
import base64
from io import BytesIO
from openai import OpenAI

# Initialize logger for this module
logger = logging.getLogger(__name__)

class OpenAIService:
    """
    Handles interactions with the OpenAI API for text and image generation.
    """
    
    def __init__(self, api_key, image_model="gpt-image-1-mini", image_quality="medium"):
        """
        Initializes the OpenAI client.
        
        Args:
            api_key (str): The OpenAI API key.
            image_model (str): The model used for flyer image generation.
            image_quality (str): The quality setting for generated images.
        """
        self.client = OpenAI(api_key=api_key)
        self.model = "gpt-4o"  # High-reasoning model for copywriting
        self.image_model = image_model
        self.image_quality = image_quality

    async def generate_event_promo(self, event_details, system_prompt):
        """
        Generates a Telegram post and an image prompt based on event details.
        
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
        Generates a flyer image using the configured OpenAI image model.
        
        Args:
            image_prompt (str): The visual description for the flyer.
            
        Returns:
            str or None: The URL of the generated image, 
                         or "QUOTA_EXCEEDED" if limits are hit.
        """
        try:
            response = self.client.images.generate(
                model=self.image_model,
                prompt=image_prompt,
                size="1024x1024",
                quality=self.image_quality,
                n=1,
            )
            image_data = response.data[0]
            if getattr(image_data, "b64_json", None):
                image_bytes = base64.b64decode(image_data.b64_json)
                image_file = BytesIO(image_bytes)
                image_file.name = "btcgdl-flyer.png"
                return image_file

            return image_data.url
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("OpenAI Quota Exceeded during image generation.")
                return "QUOTA_EXCEEDED"
            logger.error(f"OpenAI Image Generation Error: {e}")
            return None
