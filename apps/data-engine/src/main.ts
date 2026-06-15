import "reflect-metadata"
import { NestFactory } from "@nestjs/core"
import { AppModule } from "./app.module"

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule)
  app.enableShutdownHooks()
  await app.listen(3000)
  // eslint-disable-next-line no-console
  console.log("[data-engine] listening on :3000")
}
bootstrap().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("[data-engine] fatal init error", err)
  process.exit(1)
})
