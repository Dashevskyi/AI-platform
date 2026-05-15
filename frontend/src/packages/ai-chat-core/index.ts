/**
 * @it-invest/ai-chat-core
 *
 * Headless React hooks + API client for the IT-Invest AI chat. Bring your
 * own UI library — there is no Mantine, PrimeReact, or markdown renderer
 * inside this package. Peer deps: react ^18, @tanstack/react-query ^5.
 */
export { useAiChatList } from './useAiChatList';
export type { UseAiChatListResult } from './useAiChatList';

export { useAiChatMessages } from './useAiChatMessages';
export type { UseAiChatMessagesResult } from './useAiChatMessages';

export { useAiChatAttachments } from './useAiChatAttachments';
export type { UseAiChatAttachmentsResult } from './useAiChatAttachments';

export { useAiChatArtifacts } from './useAiChatArtifacts';
export type { UseAiChatArtifactsResult } from './useAiChatArtifacts';

export { useMediaRecorder } from './useMediaRecorder';
export type { UseMediaRecorderResult, RecorderState } from './useMediaRecorder';

export { useAiChatSend } from './useAiChatSend';
export type {
  UseAiChatSendOptions,
  UseAiChatSendResult,
  AttachmentProgressEvent,
} from './useAiChatSend';

export { getAiChatApi } from './api';
export type { AiChatApi } from './api';

export type {
  Chat,
  Message,
  MessageSend,
  PaginatedResponse,
  AttachmentBrief,
  AiChatMode,
  AiChatApiVariant,
  StreamEvent,
  AttachmentStatus,
  SendArgs,
  ConnectionOptions,
  AuthMode,
  ArtifactBrief,
  ArtifactDetail,
} from './types';
