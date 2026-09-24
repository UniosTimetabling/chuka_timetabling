import React from 'react';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { COLORS } from '../theme/colors';

// One vector icon per file kind — no emojis. `kind` comes from
// detectFileKind() in utils/fileType.js. Pass `color` to override the
// per-type brand colour (e.g. white when the icon sits on a filled button).
const ICON_NAME = {
  pdf: 'file-pdf-box',
  word: 'file-word-box',
  excel: 'file-excel-box',
  powerpoint: 'file-powerpoint-box',
  image: 'file-image',
  archive: 'folder-zip',
  text: 'file-document-outline',
  file: 'file-document-outline',
  link: 'link-variant',
};

const ICON_COLOR = {
  pdf: '#D32F2F',
  word: '#2B579A',
  excel: '#1D6F42',
  powerpoint: '#D24726',
  image: COLORS.aqua,
  archive: '#8D6E63',
  text: COLORS.gray500,
  file: COLORS.gray500,
  link: COLORS.blue,
};

export default function FileTypeIcon({ kind = 'file', size = 20, color, style }) {
  return (
    <MaterialCommunityIcons
      name={ICON_NAME[kind] || ICON_NAME.file}
      size={size}
      color={color || ICON_COLOR[kind] || COLORS.gray500}
      style={style}
    />
  );
}
