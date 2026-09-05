export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  webCitations?: WebCitation[];
  created_at: string;
  logId?: string;   // 后端 chat_logs id,用于 👍/👎 反馈
  avatar?: string;  // 人格头像(博主模式/圆桌)
  errorReport?: {   // 回答异常时附带,渲染"通知开发人员"按钮
    question: string;
    error: string;
    mode: string;
  };
}

export interface WebCitation {
  index: number;   // 对应正文 [WN] 的 N
  title: string;
  url: string;
  content?: string;
}

export interface Citation {
  index: number;
  atom_id: string;
  video_id: string;
  text: string;
  start_ms: number;
  end_ms?: number;
  similarity?: number;
  source?: string;
  url?: string;      // 非视频来源(如 X 推文)的原文链接
}

export interface Conversation {
  id: string;
  title: string;
  updated_at: string;
  created_at: string;
}

export interface ChatApiRequest {
  messages: { role: string; content: string }[];
  video_ids?: string[] | null;
}

export interface ChatApiResponse {
  reply: string;
  sources: Citation[];
  webSources?: WebCitation[];
  logId?: string;
}
