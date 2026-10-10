import React from "react";

export interface ChatMessageData {
    id: string;
    role: "user" | "assistant";
    content: string;
    intent?: string | null;
    tool_status?: string | null;
}

interface ChatBubbleProps {
    message: ChatMessageData;
}

export default function ChatBubble({ message }: ChatBubbleProps) {
    const isUser = message.role === "user";
    return (
        <div className={`chat-bubble-container ${isUser ? "user" : "assistant"}`}>
            <div className="chat-bubble">
                <div className="chat-content">{message.content}</div>
                {!isUser && (message.intent || message.tool_status) && (
                    <div className="chat-meta">
                        {message.intent && <span className="meta-badge">{message.intent}</span>}
                        {message.tool_status && <span className="meta-badge">{message.tool_status}</span>}
                    </div>
                )}
            </div>
        </div>
    );
}
