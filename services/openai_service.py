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
    
    def __init__(self, api_key, image_model="gpt-image-1-mini", image_quality="medium", image_size="1024x1536"):
        """
        Initializes the OpenAI client with model and quality settings.
        """
        self.client = OpenAI(api_key=api_key)
        self.text_model = "gpt-4o"
        self.image_model = image_model
        self.image_quality = image_quality
        self.image_size = image_size

    async def generate_event_promo(self, event_details, system_prompt):
        """
        Generates a Telegram post and an image prompt based on event details.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Event Details: {event_details}"}
                ],
                response_format={ "type": "json_object" } # Force JSON output
            )
            return response.choices[0].message.content
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("⚠️  OpenAI Error: Insufficient Quota. Draft and flyer generation aborted.")
                return "QUOTA_EXCEEDED"
            logger.error(f"❌ OpenAI Text Generation Error: {e}")
            return None

    async def refine_event_promo(self, event_details, current_draft, instructions, system_prompt):
        """
        Refines an existing draft based on Admin instructions.
        """
        try:
            user_content = (
                f"Original Event Details: {event_details}\n\n"
                f"Current Draft: {current_draft}\n\n"
                f"Admin Instructions: {instructions}"
            )
            
            response = self.client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": system_prompt + "\n\nYou are now refining a draft based on specific user feedback. Keep the tone consistent but apply the requested changes exactly."},
                    {"role": "user", "content": user_content}
                ],
                response_format={ "type": "json_object" }
            )
            return response.choices[0].message.content
        except Exception as e:
            if "insufficient_quota" in str(e).lower():
                logger.warning("⚠️  OpenAI Error: Insufficient Quota during refinement.")
                return "QUOTA_EXCEEDED"
            logger.error(f"❌ OpenAI Refinement Error: {e}")
            return None

    async def generate_image(self, image_prompt):
        """
        Generates a flyer image using the configured image model and quality.
        """
        try:
            response = self.client.images.generate(
                model=self.image_model,
                prompt=image_prompt,
                size=self.image_size,
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
                logger.warning("⚠️  OpenAI Error: Insufficient Quota for image generation.")
                return "QUOTA_EXCEEDED"
            logger.error(f"❌ OpenAI Image Generation Error: {e}")
            return None

    async def transcribe_voice(self, voice_file):
        """
        Transcribes audio bytes into text using OpenAI Whisper.
        """
        try:
            # Whisper expects a file-like object with a name
            if not hasattr(voice_file, "name"):
                voice_file.name = "audio.ogg"
            
            transcript = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=voice_file
            )
            return transcript.text
        except Exception as e:
            logger.error(f"❌ OpenAI Transcription Error: {e}")
            return None
