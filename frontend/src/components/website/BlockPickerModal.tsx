import React from "react";
import { BlockItem, BlockType } from "../../types";

interface BlockPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAddBlock: (block: BlockItem) => void;
}

interface BlockCatalogEntry {
  type: BlockType;
  title: string;
  description: string;
  icon: string;
  defaultContent: Record<string, any>;
}

const BLOCK_CATALOG: BlockCatalogEntry[] = [
  {
    type: "hero",
    title: "Hero Banner",
    description: "High-impact headline, subheadline, background image, and call-to-action button.",
    icon: "🚀",
    defaultContent: {
      headline: "Welcome to Our New Website",
      subheadline: "Crafting digital experiences that empower businesses and creators.",
      alignment: "center",
      cta_button: {
        label: "Get Started",
        target_url: "#contact",
        style: "primary",
      },
    },
  },
  {
    type: "text_section",
    title: "Text Section",
    description: "Rich editorial copy, story paragraphs, or detailed markdown explanations.",
    icon: "📝",
    defaultContent: {
      heading: "About Our Story",
      body: "We are committed to delivering the highest quality digital products and services. Our team works across design, engineering, and digital growth to bring visionary projects to life.",
    },
  },
  {
    type: "features_grid",
    title: "Features Grid",
    description: "Multi-column grid showcasing capabilities, benefits, or services with icons.",
    icon: "⚡",
    defaultContent: {
      title: "Core Platform Capabilities",
      items: [
        {
          title: "Speed & Reliability",
          description: "Sub-second response times and high availability across the globe.",
          icon: "zap",
        },
        {
          title: "Enterprise Security",
          description: "Strict isolation, encrypted tokens, and robust role-based access control.",
          icon: "shield",
        },
        {
          title: "Actionable Analytics",
          description: "In-depth insights, visitor metrics, and performance tracking.",
          icon: "chart",
        },
      ],
    },
  },
  {
    type: "image_gallery",
    title: "Image Gallery",
    description: "Responsive visual grid showcasing photos, portfolio work, or creative assets.",
    icon: "🖼️",
    defaultContent: {
      title: "Visual Showcase",
      images: [
        {
          image_url: "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=800",
          alt_text: "Digital analytics dashboard screenshot",
          caption: "Data-driven platform insights",
        },
        {
          image_url: "https://images.unsplash.com/photo-1551434678-e076c223a692?w=800",
          alt_text: "Collaborative team meeting in studio",
          caption: "Creative engineering teamwork",
        },
      ],
    },
  },
  {
    type: "contact_form",
    title: "Contact Form",
    description: "Interactive inquiry capture form with name, email, phone, and message fields.",
    icon: "✉️",
    defaultContent: {
      title: "Get in Touch",
      submit_label: "Send Inquiry",
      fields: ["name", "email", "phone", "message"],
    },
  },
  {
    type: "cta_banner",
    title: "CTA Highlight Banner",
    description: "Conversion-focused highlight banner with prominent call-to-action button.",
    icon: "🎯",
    defaultContent: {
      title: "Ready to Start Building?",
      description: "Join hundreds of innovators building high-performance digital presence today.",
      button: {
        label: "Contact Us Now",
        target_url: "/contact",
        style: "primary",
      },
    },
  },
  {
    type: "ad_placement",
    title: "Ad Placement Placeholder",
    description: "Non-billable visual ad slot preview. Displays placeholder during draft editing.",
    icon: "📢",
    defaultContent: {
      placement_location: "in_article",
      format: 1,
      ad_slot_id: null,
    },
  },
  {
    type: "footer",
    title: "Footer & Navigation",
    description: "Bottom navigation links, social mentions, and copyright statement.",
    icon: "⚓",
    defaultContent: {
      copyright_text: `© ${new Date().getFullYear()} Rivinity Website. All rights reserved.`,
      links: [
        { label: "Home", target_url: "/" },
        { label: "About", target_url: "/about" },
        { label: "Contact", target_url: "/contact" },
      ],
    },
  },
];

export const BlockPickerModal: React.FC<BlockPickerModalProps> = ({
  isOpen,
  onClose,
  onAddBlock,
}) => {
  if (!isOpen) return null;

  const handleSelect = (catalog: BlockCatalogEntry) => {
    const id = `blk_${catalog.type}_${Date.now().toString(36).slice(-5)}`;
    const newBlock: BlockItem = {
      id,
      type: catalog.type,
      is_visible: true,
      content: JSON.parse(JSON.stringify(catalog.defaultContent)),
    };
    onAddBlock(newBlock);
    onClose();
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        backgroundColor: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
        backdropFilter: "blur(2px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid="block-picker-modal"
    >
      <div
        className="card"
        style={{
          width: "100%",
          maxWidth: "680px",
          maxHeight: "85vh",
          overflowY: "auto",
          boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
          padding: "1.5rem",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <div>
            <h2 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>Add Content Block</h2>
            <p style={{ margin: "0.25rem 0 0", fontSize: "0.8125rem", color: "var(--text-muted)" }}>
              Select a structured block to insert into your page.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            style={{ fontSize: "0.875rem", padding: "0.25rem 0.5rem" }}
          >
            ✕
          </button>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: "0.875rem",
          }}
        >
          {BLOCK_CATALOG.map((entry) => (
            <div
              key={entry.type}
              onClick={() => handleSelect(entry)}
              style={{
                display: "flex",
                gap: "0.875rem",
                padding: "1rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--border)",
                backgroundColor: "var(--bg-card)",
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "var(--primary)";
                e.currentTarget.style.transform = "translateY(-1px)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "var(--border)";
                e.currentTarget.style.transform = "none";
              }}
              data-testid={`add-block-${entry.type}`}
            >
              <div
                style={{
                  fontSize: "1.75rem",
                  width: "44px",
                  height: "44px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "var(--bg-secondary)",
                  borderRadius: "var(--radius-sm)",
                  flexShrink: 0,
                }}
              >
                {entry.icon}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: "0.9375rem", marginBottom: "0.25rem" }}>
                  {entry.title}
                </div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", lineHeight: 1.4 }}>
                  {entry.description}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
