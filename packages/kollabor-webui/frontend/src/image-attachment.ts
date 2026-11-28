// Image attachments for the composer, and their mapping to the chat input
// body. Spec: docs/specs/webui-chat-single-stream.md (#1451).

import {
  SimpleImageAttachmentAdapter,
  type Attachment,
  type CompleteAttachment,
  type ImageMessagePart,
} from "@assistant-ui/react";
import type { InputRequest } from "./chat-stream";

/** The composer's image picker: the same adapter class the runtime builds. */
export const imageAttachmentAdapter = new SimpleImageAttachmentAdapter();

type InputAttachment = NonNullable<InputRequest["attachments"]>[number];

const isComplete = (attachment: Attachment): attachment is CompleteAttachment =>
  attachment.status.type === "complete";

const imageOf = (attachment: CompleteAttachment): ImageMessagePart | undefined =>
  attachment.content.find((part): part is ImageMessagePart => part.type === "image");

// A data URL reads `data:<media type>;base64,<payload>`.
const mediaTypeOf = (dataUrl: string): string =>
  /^data:([^;,]+)/.exec(dataUrl)?.[1] ?? "application/octet-stream";

/**
 * Maps the completed image attachments of a send to the `attachments` field of
 * `POST /sessions/{id}/inputs`. Pending attachments, and completed ones with no
 * image part, are left out. Returns undefined when nothing is left, so the
 * field is omitted.
 */
export function toInputAttachments(
  attachments: readonly Attachment[] | undefined,
): InputRequest["attachments"] {
  const inputs: InputAttachment[] = [];
  for (const attachment of attachments ?? []) {
    if (!isComplete(attachment)) continue;
    const image = imageOf(attachment);
    if (!image) continue;
    inputs.push({
      name: attachment.name,
      content_type: attachment.contentType || mediaTypeOf(image.image),
      data_url: image.image,
    });
  }
  return inputs.length > 0 ? inputs : undefined;
}
