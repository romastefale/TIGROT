# =========================
# MAIN (Blindada para Webhooks no Railway)
# =========================
def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("log", start_log))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(handle_log_callback, pattern=r"^log_(ok|edit)$"))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search_music)
    )

    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_log_input),
        group=1
    )

    app.add_handler(
        CallbackQueryHandler(more_results, pattern="^more$")
    )

    app.add_handler(
        CallbackQueryHandler(select_track, pattern=r"^track_\d+$")
    )

    if WEBHOOK_URL:
        # Previne erros de barra dupla caso você tenha colocado '/' no final da variável no Railway
        clean_webhook_url = WEBHOOK_URL.rstrip("/")
        full_webhook_url = f"{clean_webhook_url}/{TOKEN}"
        
        logger.info(f"Iniciando modo WEBHOOK.")
        logger.info(f"Listen: 0.0.0.0 | Port: {PORT} | Path: {TOKEN[:5]}...")
        logger.info(f"Webhook URL configurada para: {clean_webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=full_webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
    else:
        logger.info("WEBHOOK_URL não encontrada. Iniciando modo POLLING.")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
