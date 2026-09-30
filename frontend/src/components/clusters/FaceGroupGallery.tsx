import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  ClusterFace,
  FaceImage,
  fetchImageDetail,
} from "../../utils/api";
import FullscreenImageGallery from "../people/FullscreenImageGallery";

interface FaceGroupGalleryProps {
  faces: ClusterFace[];
  initialFaceId: number;
  initialImage: FaceImage;
  contextLabel: string;
  groupLabel: string;
  onClose: () => void;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
}

const FaceGroupGallery: React.FC<FaceGroupGalleryProps> = ({
  faces,
  initialFaceId,
  initialImage,
  contextLabel,
  groupLabel,
  onClose,
  onNavigateToCluster,
}) => {
  const initialIndex = Math.max(
    0,
    faces.findIndex((face) => face.id === initialFaceId),
  );
  const [activeIndex, setActiveIndex] = useState(initialIndex);
  const [activeImage, setActiveImage] = useState(initialImage);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const loadingRef = useRef(false);
  const imageCacheRef = useRef(new Map<number, FaceImage>([[initialImage.id, initialImage]]));
  const requestCacheRef = useRef(new Map<number, Promise<FaceImage>>());

  const loadImage = useCallback((imageId: number) => {
    const cached = imageCacheRef.current.get(imageId);
    if (cached) return Promise.resolve(cached);
    const pending = requestCacheRef.current.get(imageId);
    if (pending) return pending;
    const request = fetchImageDetail(imageId)
      .then((image) => {
        imageCacheRef.current.set(imageId, image);
        requestCacheRef.current.delete(imageId);
        return image;
      })
      .catch((error) => {
        requestCacheRef.current.delete(imageId);
        throw error;
      });
    requestCacheRef.current.set(imageId, request);
    return request;
  }, []);

  const changeFace = useCallback(
    async (nextIndex: number) => {
      if (loadingRef.current || !faces[nextIndex]) return;
      loadingRef.current = true;
      setIsLoading(true);
      setLoadError(null);
      try {
        const image = await loadImage(faces[nextIndex].image_id);
        setActiveImage(image);
        setActiveIndex(nextIndex);
      } catch (error) {
        setLoadError(
          error instanceof Error
            ? error.message
            : "Das Bild zu diesem Gesicht konnte nicht geladen werden.",
        );
      } finally {
        loadingRef.current = false;
        setIsLoading(false);
      }
    },
    [faces, loadImage],
  );

  useEffect(() => {
    if (faces.length < 2) return;
    const neighbourIndices = [
      (activeIndex + 1) % faces.length,
      (activeIndex - 1 + faces.length) % faces.length,
    ];
    neighbourIndices.forEach((index) => {
      void loadImage(faces[index].image_id).catch(() => undefined);
    });
  }, [activeIndex, faces, loadImage]);

  const activeFace = faces[activeIndex];
  return (
    <>
      <FullscreenImageGallery
        images={[activeImage]}
        activeIndex={0}
        onChange={() => undefined}
        onClose={onClose}
        onNavigateToCluster={onNavigateToCluster}
        sequence={{
          activeIndex,
          length: faces.length,
          onChange: (index) => void changeFace(index),
          highlightedFaceId: activeFace.id,
          itemLabel: "Gesicht",
          contextLabel,
          groupLabel,
          loading: isLoading,
          error: loadError,
        }}
      />
    </>
  );
};

export default FaceGroupGallery;
