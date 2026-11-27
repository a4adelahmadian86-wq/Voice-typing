# FARAST AI Gateway — Runflare domestic bridge

این سرویس درگاه داخلی و بدون state برای FARAST است. Laravel در صورت در دسترس نبودن درگاه اصلی Cloudflare Worker می‌تواند به این endpoint وصل شود؛ این سرویس درخواست چت را به همان Worker بیرونی منتقل می‌کند و خودش مدل زبانی اجرا نمی‌کند.

## نقش و محدودیت

- این سرویس VPN، پراکسی عمومی یا راهکار دورزدن محدودیت قانونی ارائه‌دهنده نیست؛ فقط API gateway احراز هویت‌شدهٔ FARAST است.
- مسیر چت: `POST /v1/chat/completions`؛ درخواست را به `FARAST_UPSTREAM_GATEWAY_URL` می‌فرستد.
- توکن ورودی Laravel با `FARAST_GATEWAY_TOKEN` و توکن خروجی به Worker با `FARAST_UPSTREAM_GATEWAY_TOKEN` جدا هستند.
- هیچ فایل، دانش، حافظهٔ کاربر یا دیتابیس روی Runflare ذخیره نمی‌شود.
- اگر اینترنت بین‌الملل قطع شود، این پل به Worker خارجی نمی‌رسد و پاسخ AI متوقف می‌شود؛ jobهای پایدار در Laravel باید retry شوند.
- هیچ درخواست خصوصی یا پروفایل رفتاری کاربر را به APIهای رایگان ناشناخته نفرستید.

## مسیرها

- `GET /health`: زنده بودن سرویس، بدون افشای secret.
- `GET /ready`: وضعیت آماده بودن قابلیت‌های gateway.
- `GET /v1/status`: وضعیت احراز هویت‌شدهٔ gateway، مدل و سرویس‌های صوتی.
- `POST /v1/chat/completions`: OpenAI-compatible؛ درخواست‌ها به Worker بالادستی می‌روند و پاسخ JSON یا SSE عبور داده می‌شود.
- `POST /v1/transcribe`: مسیر مستقل ASR؛ Groq فقط در صورت تنظیم کلید استفاده می‌شود.
- `POST /v1/synthesize`: TTS با Edge TTS و fallback اختیاری Render.

تمام endpointهای `/v1/*` به `Authorization: Bearer FARAST_GATEWAY_TOKEN` نیاز دارند.

## پیکربندی

از `.env.example` استفاده کنید:

- `FARAST_GATEWAY_TOKEN`: secret ورودی از Laravel.
- `FARAST_UPSTREAM_GATEWAY_URL`: URL دقیق Worker مستقرشده در Cloudflare.
- `FARAST_UPSTREAM_GATEWAY_TOKEN`: همان token مورد انتظار Worker.
- `AI_PRIMARY_PROVIDER=upstream`: مسیر پیش‌فرض.
- `GEMINI_CHAT_ENABLED=false` و `GROQ_CHAT_ENABLED=false`: fallbackهای مدل مستقیم غیرفعال‌اند؛ فقط پس از بررسی سیاست داده و شرایط منطقه‌ای می‌توان آن‌ها را جداگانه فعال کرد.

مدل محلی Qwen3 1.7B در پوشهٔ `runflare-llm` به‌عنوان کد قبلی نگهداری شده، اما انتخاب این پروژه نیست و اسکریپت استقرار پیش‌فرض آن را deploy نمی‌کند.

## استقرار

1. در Runflare یک پروژه و یک Docker service برای `runflare-gateway` بسازید.
2. `FARAST_GATEWAY_TOKEN`، `FARAST_UPSTREAM_GATEWAY_URL` و `FARAST_UPSTREAM_GATEWAY_TOKEN` را در تنظیمات محرمانهٔ سرویس قرار دهید.
3. از ریشهٔ مخزن، فایل `deploy-runflare.cmd` را اجرا کنید یا از `runflare-gateway/deploy-runflare.ps1` استفاده کنید.
4. پس از deploy، `/health`، `/ready` و درخواست واقعی چت را تست کنید.
5. URL و توکن ورودی را فقط پس از موفقیت تست در `FARAST_AI_GATEWAY_FALLBACK_URL` و `FARAST_AI_GATEWAY_FALLBACK_TOKEN` سمت Laravel قرار دهید.

## تمدید پس از سه ماه

Runflare CLI می‌تواند فایل‌ها را به پروژه/سرویس انتخاب‌شده deploy کند، اما مستندات رسمی فرمانی برای ساخت خودکار پروژه و سرویس جدید ارائه نمی‌کنند. پس از حذف سه‌ماهه، اگر پروژه یا سرویس قبلی دیگر وجود نداشته باشد، مالک باید ابتدا پروژه و سرویس جدید را در پنل Runflare بسازد؛ سپس فایل CMD deploy را اجرا می‌کند. اسکریپت هیچ داده‌ای را روی Runflare بازیابی نمی‌کند، چون تمام داده‌های دائمی باید خارج از آن باشند.

منابع CLI:
- https://runflare.com/docs/get-started/
- https://runflare.com/docs/work-with-cli/cli-commands/
- https://runflare.com/docs/work-with-cli/cli-deploy/

این پل برای plan رایگانِ سه‌ماهه، فقط مسیر پردازش stateless است؛ Production و uptime تضمین‌شده محسوب نمی‌شود.
