import React, { useRef, useEffect } from "react";
import ChatBubble, { ChatMessageData } from "./ChatBubble";

interface ChatWindowProps {
    messages: ChatMessageData[];
    onSendMessage: (msg: string) => void;
    isLoading: boolean;
    error: string | null;
}

export default function ChatWindow({ messages, onSendMessage, isLoading, error }: ChatWindowProps) {
    const [inputValue, setInputValue] = React.useState("");
    const endOfMessagesRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isLoading]);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!inputValue.trim() || isLoading) return;
        onSendMessage(inputValue);
        setInputValue("");
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit(e);
        }
    };

    return (
        <div className="chat-window card">
            <div className="chat-messages">
                {messages.map((msg) => (
                    <ChatBubble key={msg.id} message={msg} />
                ))}
                {isLoading && (
                    <div className="chat-bubble-container assistant">
                        <div className="chat-bubble loading">
                            <span className="dot"></span>
                            <span className="dot"></span>
                            <span className="dot"></span>
                        </div>
                    </div>
                )}
                <div ref={endOfMessagesRef} />
            </div>
            
            {error && <div className="error chat-error">{error}</div>}

            <form className="chat-input-area" onSubmit={handleSubmit}>
                <textarea
                    value={inputValue}
                    onChange={(e) => setInputValue(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Type your message here... (Press Enter to send)"
                    disabled={isLoading}
                    rows={2}
                    aria-label="Chat input"
                />
                <button type="submit" className="btn primary" disabled={isLoading || !inputValue.trim()}>
                    Send
                </button>
            </form>
        </div>
    );
}
